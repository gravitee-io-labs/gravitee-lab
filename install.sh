#!/bin/sh
# Gravitee Lab on macOS and Linux: from a bare machine to the Lab UI in your browser, and the command gravitee-lab for next
# time. In a terminal (no sudo: it asks when a step needs it):
#
#   curl -fsSL https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.sh | sh
#
# It uses what the machine has and adds only what is missing: the tools the lab needs (on macOS, Apple's command line tools;
# on Linux, from the package manager, after asking), the clone in ~/gravitee-lab and its settings, the command gravitee-lab in
# ~/.local/bin and, when that folder is not on the PATH, the line of your shell's profile that puts it there. Then it opens the
# Lab UI, which checks Docker and says how to install or start it. It never deletes anything, and can run again at any time.
#
# GRAVITEE_LAB_DIR names the folder of the clone, GRAVITEE_LAB_REPOSITORY and GRAVITEE_LAB_BRANCH what to clone (a fork, a
# branch), GRAVITEE_LAB_YES=1 answers yes to its questions (with no terminal to ask in), and GRAVITEE_LAB_NO_START=1 stops
# before the Lab UI. NO_COLOR=1 turns the colors off, and CI=true prints plain lines. On Windows, use install.ps1.
#
# Everything is in functions that run on the last line: a download cut halfway runs nothing.

docs=https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs
marker='# Added by Gravitee Lab'

# The look. In a terminal, a step shows a live line while it works: a spinner, what it does now in a few words (a bar when its
# tool counts: packages, a download) and the time, then one line with its outcome. What the tools write shows only when one
# fails. Elsewhere (a log, CI), only the outcome lines. ASCII when the terminal is not UTF-8, no color with NO_COLOR
looks() {
    live=false
    [ -t 1 ] && [ "${TERM:-dumb}" != dumb ] && live=true
    case ${CI:-} in '' | false | 0) ;; *) live=false ;; esac
    e=$(printf '\033')
    if $live; then
        clear="${e}[2K" hide="${e}[?25l" show="${e}[?25h"
    else
        clear='' hide='' show=''
    fi
    if $live && [ -z "${NO_COLOR:-}" ]; then
        bold="${e}[1m" dim="${e}[2m" red="${e}[31m" green="${e}[32m" yellow="${e}[33m" cyan="${e}[36m" off="${e}[0m"
    else
        bold='' dim='' red='' green='' yellow='' cyan='' off=''
    fi
    case ${LC_ALL:-${LC_CTYPE:-${LANG:-}}} in
        *[Uu][Tt][Ff]-8* | *[Uu][Tt][Ff]8*)
            frames='⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏' good='✔' warn='▲' bad='✖' halted='■' ask_mark='?' more='…'
            if [ -n "$cyan" ]; then full='━' empty='━'; else full='█' empty='░'; fi
            ;;
        *)
            # shellcheck disable=SC2089 # the backslash is a frame
            frames='- \ | /' good='+' warn='!' bad='x' halted='-' ask_mark='?' more='...' full='#' empty='-'
            ;;
    esac
    tick=0.1
    sleep 0.1 2> /dev/null || tick=1
    parent=$$ spinner='' label='' asking=false
    if ! notes=$(mktemp) || ! out=$(mktemp); then
        echo 'mktemp failed: is /tmp writable?' >&2
        exit 1
    fi
}

# The width of the terminal, read again as it can change
width() {
    cols=$({ stty size < /dev/tty; } 2> /dev/null) && cols=${cols#* }
    case $cols in '' | *[!0-9]* | 0) cols=$(tput cols 2> /dev/null) ;; esac
    case $cols in '' | *[!0-9]* | 0) cols=80 ;; esac
    echo "$cols"
}

# The width a line folds at: the terminal's, none in a log
room() {
    if $live; then width; else echo 1000; fi
}

# Seconds since $1, as people read them
took() {
    s=$(($(date +%s) - $1))
    if [ $s -ge 60 ]; then printf '%dm %02ds' $((s / 60)) $((s % 60)); else printf '%ds' $s; fi
}

# $1 in $2 characters at most, with an ellipsis when it is cut
fit() {
    if [ ${#1} -le "$2" ]; then
        printf '%s' "$1"
    else
        printf '%s%s' "$(printf '%s' "$1" | LC_ALL=C cut -c "1-$(($2 - ${#more}))")" "$more"
    fi
}

# What the tool of the step is doing, read from what it wrote: "count|done|total|verb|what" when it counts (packages, or a
# percentage of a download), "||verb|" for a phase it does not count, nothing when it wrote nothing known. Git's clone, and
# the package managers: apt-get, dnf (4 and 5), yum, zypper, pacman, apk. Git's phases make one bar that only goes forward
measure() {
    LC_ALL=C tr '\r' '\n' < "$out" 2> /dev/null | LC_ALL=C awk '
        function bare(s) {   # a package without its version, its architecture or its file extension
            sub(/^[ \t:]+/, "", s); sub(/[ \t].*/, "", s); sub(/-[0-9].*/, "", s); sub(/:.*/, "", s); sub(/\.rpm$/, "", s)
            return s
        }
        function percent(s) { sub(/%.*/, "", s); sub(/.*[^0-9]/, "", s); return s + 0 }
        /^remote: |^Cloning into / { next }
        /^Receiving objects: / {
            kind = "percent"; done = int(percent($0) * 90 / 100); verb = "Downloading"
            # Git says the size and the speed once a second: the last ones stay until the next
            if (match($0, /[0-9.]+ [KMG]iB [|] [0-9.]+ [KMG]iB\/s/)) { rate = substr($0, RSTART, RLENGTH); sub(/ [|] /, ", ", rate) }
            what = rate
            next
        }
        /^Resolving deltas: / { kind = "percent"; done = 90 + int(percent($0) * 8 / 100); verb = "Unpacking"; what = ""; next }
        /^Updating files: / { kind = "percent"; done = 98 + int(percent($0) * 2 / 100); verb = "Writing the files"; what = ""; next }
        / newly installed/ { apt = 1; kind = "count"; done = 0; total = $1 + $3; verb = "Downloading"; what = ""; next }
        apt && /^Get:[0-9]+ / { got++; kind = "count"; done = got; verb = "Downloading"; what = bare($5); next }
        apt && /^Unpacking / { unpacked++; kind = "percent"; done = int((unpacked + set) * 50 / total); verb = "Installing"; what = bare($2); next }
        apt && /^Setting up / { set++; kind = "percent"; done = int((unpacked + set) * 50 / total); verb = "Installing"; what = bare($3); next }
        apt { next }
        /Downloading Packages/ { kind = ""; verb = "Downloading"; what = ""; next }
        match($0, /[[(] *[0-9]+\/[0-9]+[])]/) || match($0, / [0-9]+\/[0-9]+ *$/) {
            count = substr($0, RSTART, RLENGTH); gsub(/[^0-9\/]/, "", count); split(count, part, "/")
            rest = substr($0, 1, RSTART - 1) " " substr($0, RSTART + RLENGTH)
            verb = "Downloading"
            if (rest ~ /[Ii]nstall|[Uu]pgrad|[Pp]reparing|scriptlet|[Cc]leanup|[Ee]rasing/) verb = "Installing"
            if (rest ~ /[Vv]erif|[Cc]hecking/) verb = "Checking"
            gsub(/Retrieving:?|[Ii]nstalling|[Uu]pgrading|[Pp]reparing|Running scriptlet|[Vv]erifying|[Cc]leanup|[Ee]rasing/, "", rest)
            kind = "count"; done = part[1] + 0; total = part[2] + 0; what = bare(rest); counting = 1
            if (rest ~ /Verify package|Prepare transaction/) { verb = "Installing"; what = "" }
            if (what == "Total") what = ""
            next
        }
        counting { next }
        /InRelease|Packages|package lists|state information|[Rr]epositor|metadata|Synchroniz|APKINDEX|^fetch |[0-9] [KMG]i?B\/s/ {
            kind = ""; verb = "Updating the package lists"; what = ""; next
        }
        /^Retrieving: / { kind = ""; verb = "Downloading"; what = bare(substr($0, 12)); next }
        / downloading/ { kind = ""; verb = "Downloading"; what = bare($1); next }
        END { if (verb != "") printf "%s|%s|%s|%s|%s\n", kind, done, (kind == "percent" ? 100 : total), verb, what }'
}

# The live line after the spinner and the step: a bar with its count and what it does, or the last note; then the time
detail() {
    time=$(took "$started")
    room=$(($1 - 22 - ${#time}))
    [ $room -ge 10 ] || room=10
    IFS='|' read -r kind at total verb what << EOF
$(measure)
EOF
    if [ -n "$kind" ] && [ "${total:-0}" -gt 0 ] && [ $room -ge 30 ]; then
        size=24
        [ $room -ge 60 ] || size=10
        filled=$((at * size / total))
        [ $filled -le $size ] || filled=$size
        i=0 done_part='' rest_part=''
        while [ $i -lt $size ]; do
            if [ $i -lt $filled ]; then done_part="$done_part$full"; else rest_part="$rest_part$empty"; fi
            i=$((i + 1))
        done
        if [ "$kind" = percent ]; then count="$at%"; else count="$at/$total"; fi
        text=$(fit "$verb${what:+ $what}" $((room - size - ${#count} - 3)))
        printf '%s%s%s%s%s %s  %s%s  %s%s' "$cyan" "$done_part" "$off$dim" "$rest_part" "$off" "$count" "$dim" "$text" "$time" "$off"
    else
        [ -n "$verb" ] || verb=$(tail -n 1 "$notes") what=''
        printf '%s%s  %s%s' "$dim" "$(fit "$verb${what:+ $what}" $room)" "$time" "$off"
    fi
}

# The live line, redrawn in the background until the step ends (or this script, if it is killed)
spin() {
    trap - EXIT INT
    trap 'exit 0' TERM
    # shellcheck disable=SC2086,SC2090 # one word per frame
    set -- $frames
    n=0
    while kill -0 "$parent" 2> /dev/null; do
        [ $((n % 3)) != 0 ] || line=$(detail "$(width)")
        printf '\r%s  %s%s%s %s%-13s%s %s' "$clear" "$cyan" "$1" "$off" "$bold" "$label" "$off" "$line"
        frame=$1
        shift
        set -- "$@" "$frame"
        n=$((n + 1))
        sleep "$tick"
    done
}

resume() {
    if $live && [ -z "$spinner" ] && [ -n "$label" ]; then
        spin &
        spinner=$!
    fi
}

calm() {
    if [ -n "$spinner" ]; then
        kill "$spinner" 2> /dev/null
        wait "$spinner" 2> /dev/null
        spinner=''
        printf '\r%s' "$clear"
    fi
}

# A step: its name, and what it does first
step() {
    label=$1 started=$(date +%s)
    : > "$out"
    printf '%s\n' "$2" > "$notes"
    resume
}

# What the step does now, in a few words: on the live line, or on a line of its own when there is none. A new note
# replaces what its tool said
note() {
    : > "$out"
    printf '%s\n' "$1" >> "$notes"
    $live || printf '    %s\n' "$1"
}

# A command of the step: what it writes feeds the live line, and is kept for the message of a failure
quiet() {
    "$@" >> "$out" 2>&1
}

# The outcome of the step: its mark, its name, what it found or did, and its time when that was long enough to notice
ended() {
    calm
    time=$(took "$started")
    case $time in 0s | 1s) time='' ;; esac
    # What it found, folded between words under itself when the terminal is too narrow for it
    room=$(($(room) - 18))
    [ -z "$time" ] || room=$((room - ${#time} - 2))
    [ "$room" -ge 20 ] || room=20
    found=$(printf '%s\n' "$3" | fold -s -w "$room" | sed 's/ *$//')
    printf '  %s%s%s %s%-13s%s %s%s%s%s\n' "$2" "$1" "$off" "$bold" "$label" "$off" "$(printf '%s\n' "$found" | sed -n 1p)" "${time:+  }" "$dim" "$time$off"
    printf '%s\n' "$found" | sed '1d;s/^/                  /'
    label=''
}
passed() { ended "$good" "$green" "$1"; }
warned() { ended "$warn" "$yellow" "$1"; }

# A paragraph, folded to the terminal and indented
paragraph() {
    printf '%s\n' "$2" | fold -s -w $(($(width) - 4)) | sed "s/^/  $1/;s/\$/$off/"
}

# The end: the step that failed with the last lines of its tool, what to do, and the page that says more
halt() {
    if [ -n "$label" ]; then
        ended "$bad" "$red" ''
        if [ -s "$out" ]; then
            printf '\n'
            LC_ALL=C tr '\r' '\n' < "$out" | grep -v '^ *$' | tail -n 6 | LC_ALL=C cut -c "1-$(($(width) - 6))" \
                | sed "s/^/    $dim/;s/\$/$off/"
        fi
    fi
    printf '\n'
    paragraph "$red" "$1"
    printf '  %sHelp: %s/%s%s\n\n' "$dim" "$docs" "${2:-installation.md}" "$off"
    exit 1
}

# A failure its tool explains in words that are known: what it means, in a sentence; nothing for one that is not known
cause() {
    LC_ALL=C tr '\r' '\n' < "$out" 2> /dev/null | LC_ALL=C awk '{ $0 = tolower($0) }
        /ssl certificate problem|certificate verify failed|server certificate verification failed|unable to get local issuer|self.signed certificate/ { print "proxy"; exit }
        /could not resolve|temporary failure resolving|name or service not known|failed to connect|connection timed out|network is unreachable|cannot download repomd|curl error/ { print "network"; exit }
        /remote branch .* not found/ { print "branch"; exit }
        /repository not found|repository .* does not exist|could not read username|authentication failed|does not appear to be a git repository/ { print "repository"; exit }
        /dpkg was interrupted/ { print "dpkg"; exit }
        /could not get lock|unable to acquire the dpkg|is held by process|waiting for cache lock|holding the lock|unable to lock database|system management is locked/ { print "lock"; exit }
        /no space left on device|not enough free space|insufficient space/ { print "disk"; exit }'
}

# The end of a step whose tool failed: what its words mean when they are known, else $1, with the page of $2
failed() {
    case $(cause) in
        proxy) halt 'A proxy inspects the HTTPS of this network: git and the package manager need the certificate of your company.' 'troubleshooting.md#corporate-proxy-or-offline-use' ;;
        network) halt 'This machine does not reach the internet: check the connection, or a VPN that blocks GitHub or the package mirrors, then run this command again.' 'installation.md#prerequisites' ;;
        branch) halt "$repository has no branch $branch: name one it has with GRAVITEE_LAB_BRANCH." ;;
        repository) halt "$repository does not exist, or is private: check GRAVITEE_LAB_REPOSITORY." ;;
        dpkg) halt 'An earlier install was stopped halfway: run sudo dpkg --configure -a, then run this command again.' 'installation.md#linux' ;;
        lock) halt 'Another install is running, often the automatic updates: wait until it ends, then run this command again.' 'installation.md#linux' ;;
        disk) halt 'The disk is full: free some space, then run this command again.' 'installation.md#prerequisites' ;;
        *) halt "$1" "$2" ;;
    esac
}

# A question, answered from the terminal: the script itself is what `curl | sh` gives as input. Yes with GRAVITEE_LAB_YES=1;
# no when there is no terminal to ask in, and then `unasked` says how to answer
ask() {
    unasked=''
    [ "${GRAVITEE_LAB_YES:-}" = 1 ] && return 0
    if ! { true < /dev/tty; } 2> /dev/null; then
        unasked=' With no terminal to ask in, GRAVITEE_LAB_YES=1 answers yes.'
        return 1
    fi
    calm
    asking=true
    printf '  %s%s%s %s %s(Y/n)%s %s' "$cyan" "$ask_mark" "$off" "$1" "$dim" "$off" "$show"
    read -r answer < /dev/tty || answer=n
    asking=false
    # A terminal whose erase key is not the one of the tty (the classic Windows console sends ^H) leaves it in the answer
    answer=$(printf '%s\n' "$answer" | awk '{
        for (i = 1; i <= length($0); i++) {
            c = substr($0, i, 1)
            if (c == "\b" || c == "\177") kept = substr(kept, 1, length(kept) - 1)
            else if (c !~ /[[:cntrl:]]/) kept = kept c
        }
        print kept
    }')
    printf '%s' "$hide"
    case $answer in
        '' | [yY]*) resume ;;
        *) return 1 ;;
    esac
}

# Ctrl+C: the step that ran ends on its own mark, and what is done stays done
stopped() {
    if $asking; then printf '\n'; fi
    [ -z "$label" ] || ended "$halted" "$yellow" 'Stopped'
    finish
    printf '\n  Run the command again to go on: what is done stays done.\n\n'
    exit 130
}

# Python 3.9 or later, as python3 or beside an older one (python3.11): the Makefile looks for the same names
python_ok() {
    for python in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
        command -v "$python" > /dev/null 2>&1 && "$python" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2> /dev/null && return
    done
    return 1
}

# The tools of the lab that are missing, by the name of their command
missing() {
    for tool in git make openssl; do
        command -v "$tool" > /dev/null 2>&1 || printf '%s ' "$tool"
    done
    python_ok || printf 'python3 '
}

# Words as a person lists them: "git", "git and make", "git, make and openssl"
listed() {
    echo "$1" | sed 's/ *$//;s/ /, /g;s/\(.*\), /\1 and /'
}

# The tools found, with their versions
versions() {
    python_ok
    printf 'Git %s, Make %s, Python %s, %s' "$(git --version | awk '{print $3}')" \
        "$(make --version 2> /dev/null | awk 'NR == 1 {print $NF}')" \
        "$("$python" -c 'import platform; print(platform.python_version())')" "$(openssl version | awk '{print $1, $2}')"
}

# macOS: Apple's command line tools bring git, make and python3, and the system has openssl. Without them, their commands are
# stubs that open the install window. The window is opened once, brought to the front (it opens behind the terminal), and
# followed: the tools once they are there, a Cancel or a failed install (a full disk) as soon as the window closes
mac_tools() {
    step Tools 'Looking for Git, Make, Python and OpenSSL'
    if ! xcode-select -p > /dev/null 2>&1; then
        installer='/System/Library/CoreServices/Install Command Line Developer Tools.app'
        xcode-select --install > /dev/null 2>&1
        note "In the window of macOS, click Install, then Agree: its command line tools, about 1 GB"
        seen=0
        waited=0
        until xcode-select -p > /dev/null 2>&1; do
            if pgrep -f "$installer" > /dev/null 2>&1; then
                seen=$((seen + 1))
                # Its window opens behind the terminal: brought forward once it is drawn
                [ $seen != 2 ] || open -a "$installer" 2> /dev/null
            elif [ $seen -gt 0 ] || [ $waited -ge 30 ]; then
                sleep 3   # the tools are written just before the window closes
                xcode-select -p > /dev/null 2>&1 && break
                halt "The command line tools are not installed: their window was closed before the end, or macOS could not install them (they need a few GB of free disk). Run this command again to try again." 'installation.md#macos'
            fi
            sleep 2
            waited=$((waited + 2))
        done
    fi
    gone=$(missing)
    [ -z "$gone" ] || halt "The command line tools are installed, yet $(listed "$gone") cannot run. Run sudo xcode-select --reset, then run this command again." 'installation.md#macos'
    passed "$(versions)"
}

# Linux: what is missing, from the package manager, as root through sudo; in WSL through wsl.exe, which makes Windows' user root
# of its distributions with no password to type (one set long ago is often forgotten). Each manager has its own name for Python
linux_tools() {
    step Tools 'Looking for Git, Make, Python and OpenSSL'
    gone=$(missing)
    if [ -z "$gone" ]; then
        passed "$(versions)"
        return
    fi
    packages=$gone
    # A system whose python3 is older than 3.9 ships a later one under another name: the first its manager has is installed
    newer=''
    if command -v apt-get > /dev/null 2>&1; then
        manager=apt-get install='apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y'
        newer='python3.12 python3.11 python3.10 python3.9'
    elif command -v dnf > /dev/null 2>&1; then
        manager=dnf install='dnf install -y'
        newer='python3.12 python3.11 python39'
    elif command -v yum > /dev/null 2>&1; then
        manager=yum install='yum install -y'
        newer='python3.11 python39'
    elif command -v zypper > /dev/null 2>&1; then
        manager=zypper install='zypper --non-interactive install'
        newer='python313 python312 python311 python310 python39'
    elif command -v pacman > /dev/null 2>&1; then
        manager=pacman install='pacman -S --needed --noconfirm'
        # A system that never synced its databases (a new container) syncs them first, and upgrades nothing
        ls /var/lib/pacman/sync/*.db > /dev/null 2>&1 || install="pacman -Sy --noconfirm && $install"
        packages=$(echo "$gone" | sed 's/python3/python/')
    elif command -v apk > /dev/null 2>&1; then
        manager=apk install='apk add'
    else
        halt "Install $(listed "$gone") with the package manager of this system, then run this command again." 'installation.md#linux'
    fi
    case $gone in *' '?*) them=them ;; *) them=it ;; esac
    if [ "$(id -u)" = 0 ]; then
        root=''
    elif [ -n "${WSL_DISTRO_NAME:-}" ] && command -v wsl.exe > /dev/null 2>&1 && wsl.exe -d "$WSL_DISTRO_NAME" -u root -- true < /dev/null > /dev/null 2>&1; then
        root="wsl.exe -d $WSL_DISTRO_NAME -u root --"
    elif command -v sudo > /dev/null 2>&1; then
        root='sudo'
    else
        halt "sudo is not here to install $(listed "$gone"): install $them as root, then run this command again." 'installation.md#linux'
    fi
    ask "The Lab needs $(listed "$gone") to run. Install $them with $manager?" \
        || halt "Install $(listed "$packages") with $manager, then run this command again.$unasked" 'installation.md#linux'
    if [ "$root" = sudo ] && ! sudo -n true 2> /dev/null; then
        calm
        printf '%s' "$show"
        # shellcheck disable=SC2024 # sudo reads the password from the terminal
        sudo -v -p "  $cyan$ask_mark$off Password of %p for sudo: " < /dev/tty \
            || halt "sudo did not take the password. Install $(listed "$packages") with $manager, then run this command again." 'installation.md#linux'
        printf '%s' "$hide"
        # A sudo that keeps no password (timestamp_timeout=0) asks again at each command: no live line hides its question
        if sudo -n true 2> /dev/null; then resume; else live=false; fi
    fi
    note "Installing $(listed "$packages") with $manager"
    # No input: nothing asks for one with -y, and wsl.exe would read the keys typed in the terminal
    # shellcheck disable=SC2086
    quiet $root sh -c "$install $packages" < /dev/null \
        || failed "$(listed "$packages") did not install: $manager says why above." 'installation.md#linux'
    if ! python_ok; then
        note 'Adding a Python later than 3.9 beside the one of this system'
        for package in $newer; do
            # shellcheck disable=SC2086
            quiet $root sh -c "$install $package" < /dev/null && python_ok && break
        done
    fi
    python_ok || halt "Python 3.9 or later is missing, and the package manager of this system has none: install one, then run this command again." 'installation.md#linux'
    gone=$(missing)
    [ -z "$gone" ] || halt "Still missing: $(listed "$gone"). Install it, then run this command again." 'installation.md#linux'
    passed "$(versions)"
}

# A folder as a person reads it: ~ for the home folder
# shellcheck disable=SC2088 # the ~ is what is shown, not a path
shown() {
    case $1 in
        "$HOME"/*) printf '~/%s\n' "${1#"$HOME"/}" ;;
        *) echo "$1" ;;
    esac
}

# The clone, then what a fresh one lacks (make init), so that the Lab UI starts with nothing to say. A clone that is there is
# used as it is, whatever its origin (a fork) or its branch, and the Lab UI updates it
clone() {
    host=${repository#*://}
    host=${host%%/*}
    case $repository in *://*) ;; *) host='the repository' ;; esac
    step 'Gravitee Lab' "Looking for $(shown "$lab")"
    if [ -e "$lab" ]; then
        if [ -f "$lab/lab-ui/server.py" ] && [ -f "$lab/bin/gravitee-lab" ] && git -C "$lab" rev-parse --git-dir > /dev/null 2>&1; then
            there=', already there'
        elif [ -f "$lab/lab-ui/server.py" ]; then
            halt "$(shown "$lab") holds a Gravitee Lab older than the command gravitee-lab. Update it (cd $(shown "$lab") && make update), then run this command again."
        else
            halt "$(shown "$lab") holds something else. Move it, or name another folder with GRAVITEE_LAB_DIR, then run this command again."
        fi
    else
        there=''
        note "Connecting to $host"
        # A repository that asks for a sign-in (a typo, a private fork) fails at once rather than ask under the live line
        GIT_TERMINAL_PROMPT=0 quiet git clone --progress --branch "$branch" "$repository" "$lab" \
            || failed "The clone of $repository failed: git says why above." 'installation.md#prerequisites'
    fi
    note 'Preparing the settings and the demo certificates'
    quiet make -C "$lab" --no-print-directory init || failed 'The settings of the lab could not be made: make init says why above.' 'installation.md#run-it'
    passed "$(shown "$lab")$there"
}

# The profile that the shell of this person reads when a terminal opens
profile() {
    case ${SHELL##*/} in
        zsh) echo "${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash) [ "$system" = macOS ] && echo "$HOME/.bash_profile" || echo "$HOME/.bashrc" ;;
        fish) echo "$HOME/.config/fish/conf.d/gravitee-lab.fish" ;;
        *) echo "$HOME/.profile" ;;
    esac
}

# The command: a link in ~/.local/bin to bin/gravitee-lab of the clone, and that folder on the PATH of the next terminals
command_on_path() {
    step Command 'Linking gravitee-lab into ~/.local/bin'
    mkdir -p "$HOME/.local/bin" || halt "$HOME/.local/bin cannot be made."
    if [ -e "$HOME/.local/bin/gravitee-lab" ] && [ ! -L "$HOME/.local/bin/gravitee-lab" ]; then
        halt "$HOME/.local/bin/gravitee-lab is a file of something else. Move it, then run this command again."
    fi
    ln -sf "$lab/bin/gravitee-lab" "$HOME/.local/bin/gravitee-lab"
    case :$PATH: in
        *:"$HOME/.local/bin":*)
            passed 'gravitee-lab'
            return
            ;;
    esac
    file=$(profile)
    if ! grep -qs "$marker" "$file"; then
        mkdir -p "$(dirname "$file")"
        # shellcheck disable=SC2016 # the profile expands $HOME and $PATH, each time a terminal opens
        case $file in
            *.fish) printf 'fish_add_path -g "$HOME/.local/bin"  %s\n' "$marker" >> "$file" ;;
            *) printf '\nexport PATH="$HOME/.local/bin:$PATH"  %s\n' "$marker" >> "$file" ;;
        esac
    fi
    fresh=' in a new terminal'
    passed "gravitee-lab (PATH set in $(shown "$file"))"
}

# Docker's command: on the PATH, or where Docker Desktop puts it when a terminal opened before it was installed does not
# find it yet (the Lab UI looks in the same places)
docker_cli() {
    for docker in "$(command -v docker 2> /dev/null)" /usr/local/bin/docker "$HOME/.docker/bin/docker" \
        /Applications/Docker.app/Contents/Resources/bin/docker; do
        [ -n "$docker" ] && [ -x "$docker" ] && echo "$docker" && return
    done
}

# Docker is only looked at: the Lab UI checks it with the rest of the machine, and shows how to install or start it
docker_state() {
    step Docker 'Looking for Docker'
    docker=$(docker_cli)
    if [ -z "$docker" ]; then
        warned 'Not installed: the Lab UI shows how to get it'
    elif version=$("$docker" version --format '{{.Server.Version}}' 2> /dev/null) && [ -n "$version" ]; then
        passed "Docker $version is running"
    else
        warned 'Not running: the Lab UI starts it, or shows how'
    fi
}

# The machine, as its owner names it
machine() {
    if [ "$system" = macOS ]; then
        printf 'macOS %s' "$(sw_vers -productVersion 2> /dev/null)"
    else
        # shellcheck disable=SC1091 # the file of the system that runs this
        (. /etc/os-release 2> /dev/null && printf '%s' "${PRETTY_NAME:-Linux}") || printf 'Linux'
        grep -qi microsoft /proc/version 2> /dev/null && printf ' on WSL'
    fi
    printf ', %s' "$(uname -m)"
}

finish() {
    calm
    printf '%s' "$show"
    rm -f "$notes" "$out"
}

main() {
    looks
    trap finish EXIT
    trap stopped INT TERM
    printf '%s' "$hide"
    case $(uname -s) in
        Darwin) system=macOS ;;
        Linux) system=Linux ;;
        MINGW* | MSYS* | CYGWIN*) halt 'On Windows, run this in PowerShell instead: irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex' 'installation.md#windows-with-wsl-2' ;;
        *) halt "Gravitee Lab runs on macOS, Linux and Windows through WSL 2, not on $(uname -s)." 'installation.md#prerequisites' ;;
    esac
    if [ -n "${SUDO_USER:-}" ]; then
        halt 'Run this without sudo: it asks for it when a step needs it, and the lab belongs to your user.'
    fi
    lab=${GRAVITEE_LAB_DIR:-$HOME/gravitee-lab}
    case $lab in
        /*) ;;
        *) lab=$PWD/$lab ;;
    esac
    branch=${GRAVITEE_LAB_BRANCH:-main}
    repository=${GRAVITEE_LAB_REPOSITORY:-https://github.com/gravitee-io-labs/gravitee-lab.git}
    begun=$(date +%s) fresh=''

    printf '\n  %sGravitee Lab%s  %s%s%s\n\n' "$bold" "$off" "$dim" "$(machine)" "$off"
    if [ "$system" = macOS ]; then mac_tools; else linux_tools; fi
    clone
    command_on_path
    docker_state

    printf '\n'
    printf '%s\n' "$good Ready in $(took "$begun"). Next time, run gravitee-lab$fresh." | fold -s -w $(($(room) - 4)) \
        | sed "s/ *\$//;1s/^\($good Ready in [^.]*\.\)/$green\1$off/;s/gravitee-lab/$bold&$off/;1s/^/  /;2,\$s/^/    /"
    if [ "${GRAVITEE_LAB_NO_START:-}" = 1 ]; then
        printf '\n'
        return
    fi
    finish
    trap - EXIT INT TERM
    exec "$lab/bin/gravitee-lab" < /dev/null
}

main "$@"
