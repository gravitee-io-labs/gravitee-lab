# Gravitee Lab on Windows: from a bare Windows to the Lab UI in your browser. The lab runs in WSL 2, in a Linux distribution,
# with Docker. In PowerShell (no administrator needed: Windows asks when a step needs it):
#
#   irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex
#
# It does what only Windows can do, then hands the distribution to install.sh, the installer of macOS and Linux, which installs
# the tools, clones the lab in ~/gravitee-lab, makes its settings and the command gravitee-lab. From Windows: WSL, a
# distribution (an Ubuntu or a Debian already there, else Ubuntu) and its Linux user, a Docker that answers in it (Docker
# Desktop when there is none), what keeps the distribution from the internet while Windows reaches it (a VPN, a proxy that
# inspects HTTPS, whose certificate Windows trusts), a Gravitee Lab shortcut in the Start menu, then the Lab UI. It uses what
# the computer has, asks before it changes what was there (a distribution of WSL 1, a Docker Desktop that runs containers,
# the certificates the distribution trusts), and never deletes anything. Run it again at any time, also after a restart it
# asks for.
#
# GRAVITEE_LAB_DISTRO names the distribution to use. GRAVITEE_LAB_BRANCH, GRAVITEE_LAB_DIR, GRAVITEE_LAB_REPOSITORY and
# GRAVITEE_LAB_YES are install.sh's, and GRAVITEE_LAB_YES=1 answers yes here too. Written for Windows PowerShell 5.1, the
# one every Windows has, in ASCII: it reads no other encoding without a byte order mark.

& {
    # Each step reads the exit code of what it runs: with errors that stop, Windows PowerShell 5.1 stops at the first line a
    # program writes to its error output
    $ErrorActionPreference = 'Continue'
    $env:WSL_UTF8 = '1'   # wsl.exe answers in UTF-16 otherwise
    $branch = if ($env:GRAVITEE_LAB_BRANCH) { $env:GRAVITEE_LAB_BRANCH } else { 'main' }
    $raw = "https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/$branch"
    $help = 'https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/installation.md#windows-with-wsl-2'
    $network = 'https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/troubleshooting.md#windows-and-wsl-2'
    $command = 'irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex'

    # The look of install.sh, so that the two read as one: two spaces, a mark, the step's name on 13 columns, what it found or
    # did. Its marks where the console draws them (Windows Terminal, a UTF-8 console), plain ones elsewhere
    $fancy = [bool]$env:WT_SESSION -or [Console]::OutputEncoding.CodePage -eq 65001
    $good, $warn, $bad = if ($fancy) { [string][char]0x2714, [string][char]0x25B2, [string][char]0x2716 } else { '+', '!', 'x' }

    function Mark($mark, $color, $label, $text) {
        Write-Host '  ' -NoNewline
        Write-Host $mark -ForegroundColor $color -NoNewline
        Write-Host (' {0,-13} ' -f $label) -ForegroundColor White -NoNewline
        Write-Host $text
    }
    function Passed($label, $text) { Mark $good Green $label $text }
    function Warned($label, $text) { Mark $warn Yellow $label $text }
    # What a step does now, for a step that takes a while
    function Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
    # The end: the step that failed, what to do, and where to read more
    function Halt($label, $text, $more) {
        if ($label) { Mark $bad Red $label '' }
        Write-Host ''
        Write-Host "  $text" -ForegroundColor Red
        Write-Host "  Help: $(if ($more) { $more } else { $help })" -ForegroundColor DarkGray
        Write-Host ''
    }
    # A stop for a step the person finishes outside, a restart or a sign-out: the command to run next, in the clipboard too
    function Again($label, $text) {
        Halt $label $text
        try { Set-Clipboard -Value $command; Write-Host "  Then run: $command   (in your clipboard)" } catch { Write-Host "  Then run: $command" }
        Write-Host ''
    }
    # A question about something the person set up: no unless they say yes
    function Yes($question) {
        if ($env:GRAVITEE_LAB_YES -eq '1') { return $true }
        Write-Host '  ' -NoNewline
        Write-Host '?' -ForegroundColor Cyan -NoNewline
        (Read-Host " $question (y/N)") -match '^[yY]'
    }

    # A script for sh in the distribution, passed in base64: Windows PowerShell 5.1 loses the quotes of an argument it
    # gives a program, and writes CRLF line ends to its input. The default user runs it unless a user is named. It gets no
    # input: wsl.exe would read what the person types for the next question.
    function Linux([string]$script, [string]$user) {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r", '')))
        $as = if ($user) { @('-u', $user) } else { @() }
        $null | & wsl.exe -d $distro @as -- sh -c "echo $encoded | base64 -d | sh"
    }

    # The distributions WSL has, by name, and the default one, from the registry: wsl.exe says which is the default in the
    # language of Windows
    function Distributions {
        $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
        $found = @{ Names = @(); Default = $null }
        if (-not (Test-Path $root)) { return $found }
        $default = (Get-ItemProperty $root -ErrorAction SilentlyContinue).DefaultDistribution
        foreach ($key in Get-ChildItem $root) {
            $name = (Get-ItemProperty $key.PSPath).DistributionName
            if ($name) {
                $found.Names += $name
                if ($key.PSChildName -eq $default) { $found.Default = $name }
            }
        }
        return $found
    }

    # The distribution to use: the one named, else the default one when it is an Ubuntu or a Debian, else the first of
    # these, else Ubuntu, which is installed. install.sh knows other package managers; these are the ones tested on Windows
    function Choose($wsl) {
        if ($env:GRAVITEE_LAB_DISTRO) { return $env:GRAVITEE_LAB_DISTRO }
        $apt = @($wsl.Names | Where-Object { $_ -match '^(Ubuntu|Debian)' })
        if ($wsl.Default -and $apt -contains $wsl.Default) { return $wsl.Default }
        if ($apt.Count) { return $apt[0] }
        return 'Ubuntu'
    }

    # Docker Desktop's command for Windows, where it installs it or on the PATH; $null without Docker Desktop
    function DesktopCli {
        $installed = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
        if (Test-Path $installed) { return $installed }
        $onPath = Get-Command docker.exe -ErrorAction SilentlyContinue
        if ($onPath -and (Test-Path (Join-Path (Split-Path (Split-Path (Split-Path $onPath.Source))) 'Docker Desktop.exe'))) {
            return $onPath.Source
        }
        return $null
    }

    function DesktopApp($cli) { Join-Path (Split-Path (Split-Path (Split-Path $cli))) 'Docker Desktop.exe' }

    # Whether a command answers within the time given, asked every few seconds, on a line that says what is awaited and since
    # when, redrawn in place in a console and left out of a log
    function WaitFor([scriptblock]$answers, [int]$seconds, $label, $text) {
        $since = Get-Date
        $live = -not [Console]::IsOutputRedirected
        while (((Get-Date) - $since).TotalSeconds -lt $seconds) {
            if (& $answers) {
                if ($live) { Write-Host ("`r" + (' ' * 78) + "`r") -NoNewline }
                return $true
            }
            if ($live) { Write-Host ("`r  {0} {1,-13} {2}  {3}s " -f '.', $label, $text, [int]((Get-Date) - $since).TotalSeconds) -NoNewline }
            Start-Sleep -Seconds 3
        }
        if ($live) { Write-Host '' }
        return $false
    }

    function LinuxDocker { Linux 'docker info >/dev/null 2>&1' | Out-Null; return $LASTEXITCODE -eq 0 }

    function WindowsDocker($cli) { & $cli info *> $null; return $LASTEXITCODE -eq 0 }

    # Whether the distribution resolves the names of sites: without, apt and git wait minutes before they fail
    function Resolves { [bool](Linux 'timeout 20 getent hosts github.com >/dev/null && echo yes') }

    # Whether the distribution trusts the sites it reaches over HTTPS, by the first client it has: ok, certificate (a proxy of
    # the company signs them), or unknown (no client yet, as on a minimal Debian, or another failure)
    function Https {
        $said = Linux ('if command -v curl >/dev/null; then curl -sS -o /dev/null --max-time 20 https://github.com 2>&1; ' +
            'elif command -v git >/dev/null; then git ls-remote https://github.com/gravitee-io-labs/gravitee-lab.git HEAD 2>&1 >/dev/null; ' +
            'elif command -v python3 >/dev/null; then python3 -c "import urllib.request as u; u.urlopen(\"https://github.com\", timeout=20)" 2>&1; ' +
            'else echo unknown; exit 1; fi')
        if ($LASTEXITCODE -eq 0) { return 'ok' }
        if (($said -join ' ') -match 'certificate|SSL|CERTIFICATE') { return 'certificate' }
        return 'unknown'
    }

    # The root certificate that Windows trusts for github.com: behind a proxy that inspects HTTPS, the company's own
    function WindowsRoot {
        try {
            $tcp = New-Object Net.Sockets.TcpClient('github.com', 443)
            try {
                $ssl = New-Object Net.Security.SslStream($tcp.GetStream())
                $ssl.AuthenticateAsClient('github.com')
                $chain = New-Object Security.Cryptography.X509Certificates.X509Chain
                [void]$chain.Build((New-Object Security.Cryptography.X509Certificates.X509Certificate2 $ssl.RemoteCertificate))
                return $chain.ChainElements[$chain.ChainElements.Count - 1].Certificate
            } finally { $tcp.Close() }
        } catch { return $null }
    }

    # That certificate added to the ones the distribution trusts, once the person says yes: whether it was
    function TrustWindowsRoot {
        $root = WindowsRoot
        if (-not $root) { return $false }
        Warned 'Certificates' "A proxy of your company inspects HTTPS with $($root.GetNameInfo('SimpleName', $false)): Windows trusts it, $distro does not"
        if (-not (Yes "Add it to the certificates $distro trusts?")) { return $false }
        $pem = "-----BEGIN CERTIFICATE-----`n" + ([Convert]::ToBase64String($root.RawData, 'InsertLineBreaks') -replace "`r", '') + "`n-----END CERTIFICATE-----`n"
        Linux ("command -v update-ca-certificates >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates >/dev/null; " +
            "mkdir -p /usr/local/share/ca-certificates && printf '%s' '$pem' > /usr/local/share/ca-certificates/windows-$($root.Thumbprint).crt && " +
            'update-ca-certificates >/dev/null 2>&1') 'root' | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        Passed 'Certificates' "$distro trusts the certificate of your company"
        return $true
    }

    $edition = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption -replace '^Microsoft ', ''
    Write-Host ''
    Write-Host '  Gravitee Lab' -ForegroundColor White -NoNewline
    Write-Host "  $edition, $env:PROCESSOR_ARCHITECTURE" -ForegroundColor DarkGray
    Write-Host ''

    # WSL, a distribution, Docker Desktop and the images of the platform take about 25 GB of the disk of Windows
    $free = [math]::Floor((Get-PSDrive $env:SystemDrive.TrimEnd(':')).Free / 1GB)
    if ($free -lt 25) {
        Warned 'Disk' "$env:SystemDrive has $free GB free, and the lab takes about 25 GB with the images of the platform"
        if (-not (Yes 'Go on anyway?')) { return Halt '' 'Free some space, then run this command again.' }
    }

    # WSL. `wsl --version` answers only from the WSL of the Store, the one Docker Desktop needs
    $version = & wsl.exe --version 2> $null
    if ($LASTEXITCODE -ne 0) {
        Note 'Installing WSL: Windows asks for administrator rights'
        $install = Start-Process wsl.exe -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait -PassThru
        if ($install.ExitCode -ne 0) {
            return Halt 'WSL' 'WSL did not install. Turn on virtualization in the firmware of this computer if WSL says so, then run this command again.'
        }
        return Again 'WSL' 'WSL is installed. Restart Windows.'
    }
    Passed 'WSL' "$([regex]::Match(($version -join ' '), '\d+(\.\d+)+').Value)"

    # The distribution and its Linux user
    $wsl = Distributions
    $distro = Choose $wsl
    if ($wsl.Names -notcontains $distro) {
        Note "Installing $distro"
        $said = & wsl.exe --install -d $distro --no-launch 2>&1
        if ($LASTEXITCODE -ne 0) { return Halt $distro "$distro did not install: $($said -join ' ')" }
    }
    if (-not ((Linux 'uname -r') -match 'WSL2')) {
        Warned $distro 'Runs on WSL 1, and Docker needs WSL 2: its files stay, and the move takes a few minutes'
        if (-not (Yes "Move $distro to WSL 2?")) {
            return Halt $distro "Move $distro to WSL 2 (wsl --set-version $distro 2), or name another distribution in GRAVITEE_LAB_DISTRO, then run this command again."
        }
        & wsl.exe --set-version $distro 2 | Out-Null
        if ($LASTEXITCODE -ne 0) { return Halt $distro "$distro could not move to WSL 2." }
    }
    $user = Linux 'getent passwd | awk -F: ''$3 >= 1000 && $3 < 65534 { print $1; exit }''' 'root'
    if (-not $user) {
        Note "$distro asks for a user name and a password for Linux. They are not your Windows ones: sudo asks for this password."
        if (Linux 'test -x /usr/lib/wsl/wsl-setup && echo yes' 'root') {
            & wsl.exe -d $distro -u root -- /usr/lib/wsl/wsl-setup
        } else {
            $name = Read-Host '  Linux user name'
            & wsl.exe -d $distro -u root -- adduser $name
            Linux "usermod -aG sudo $name && printf '\n[user]\ndefault=$name\n' >> /etc/wsl.conf" 'root'
        }
        $user = Linux 'getent passwd | awk -F: ''$3 >= 1000 && $3 < 65534 { print $1; exit }''' 'root'
        if (-not $user) { return Halt $distro "$distro has no Linux user yet. Run this command again to create one." }
    }
    # The user that /etc/wsl.conf names is the default one only once the distribution restarts, which would make Docker
    # Desktop lose it: WSL is told now, and an older WSL that cannot be is restarted. A default user set otherwise is kept.
    $named = Linux 'sed -n ''/^\[user\]/,/^\[/s/^[[:space:]]*default[[:space:]]*=[[:space:]]*//p'' /etc/wsl.conf 2>/dev/null' 'root'
    if ($named -and (Linux 'id -un') -ne $named) {
        & wsl.exe --manage $distro --set-default-user $named | Out-Null
        if ($LASTEXITCODE -ne 0) { & wsl.exe --terminate $distro | Out-Null }
    }
    Passed $distro "user $(Linux 'id -un')"

    # A Docker that answers in the distribution: the one there (Docker Desktop, Docker Engine in it...) is used as it is,
    # and install.sh says so; else Docker Desktop, installed, started and serving it
    if (-not (LinuxDocker)) {
        $cli = DesktopCli
        if (-not $cli -and (Linux 'command -v dockerd')) {
            return Halt 'Docker' "Docker Engine is installed in $distro and does not answer: start it (sudo service docker start), then run this command again."
        }
        if (-not $cli) {
            Warned 'Docker' 'Docker Desktop is not installed. At work, your company may need a Docker subscription, and your IT may install it for you'
            if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
                return Halt '' 'Install Docker Desktop (https://docs.docker.com/desktop/setup/install/windows-install/), then run this command again.'
            }
            if (-not (Yes 'Install Docker Desktop now with winget? Windows asks for administrator rights')) {
                return Halt '' 'Install Docker Desktop, then run this command again.'
            }
            & winget.exe install --exact --id Docker.DockerDesktop --source winget
            $cli = DesktopCli
            if (-not $cli) { return Halt 'Docker' 'Docker Desktop did not install.' }
            if (-not (whoami /groups | Select-String -SimpleMatch 'docker-users' -Quiet) -and (Get-LocalGroup docker-users -ErrorAction SilentlyContinue)) {
                return Again 'Docker' 'Docker Desktop is installed. Sign out of Windows and in again, so that it lets you use it.'
            }
        }
        if (-not (WindowsDocker $cli)) {
            Start-Process (DesktopApp $cli)
            Note 'The first time, accept the agreement in the window of Docker Desktop'
            if (-not (WaitFor { WindowsDocker $cli } 600 'Docker' 'Starting Docker Desktop')) {
                return Halt 'Docker' 'Docker Desktop did not start. Open it, then run this command again.'
            }
        }
        $served = WaitFor { LinuxDocker } 30 'Docker' "Waiting for Docker Desktop to serve $distro"
        $wsl = Distributions
        if (-not $served -and (-not $wsl.Default -or $wsl.Default -like 'docker-desktop*')) {
            # Docker Desktop serves the default distribution, and its own is no distribution to work in
            Note "$distro becomes the default distribution of WSL, the one Docker Desktop serves"
            & wsl.exe --set-default $distro | Out-Null
            $wsl = Distributions
            $served = WaitFor { LinuxDocker } 30 'Docker' "Waiting for Docker Desktop to serve $distro"
        }
        # It served the distribution and lost it (a link of its integration is left), which it does each time the distribution
        # restarts, or it serves the default one and has not seen it yet: a restart of Docker Desktop serves it again
        if (-not $served -and ($wsl.Default -eq $distro -or (Linux 'test -L /usr/bin/docker && echo yes'))) {
            $running = @(& $cli ps -q 2> $null).Count
            if ($running) {
                Warned 'Docker' "Docker Desktop must restart to reach $distro, which stops the $running containers that run now"
                if (-not (Yes 'Restart Docker Desktop now?')) {
                    return Halt '' 'Restart Docker Desktop when you can, then run this command again.'
                }
            }
            & $cli desktop restart *> $null
            $served = WaitFor { LinuxDocker } 120 'Docker' 'Restarting Docker Desktop'
        }
        if (-not $served) {
            Start-Process (DesktopApp $cli)
            Note "In Docker Desktop, open Settings > Resources > WSL integration, turn on $distro, then Apply"
            if (-not (WaitFor { LinuxDocker } 900 'Docker' "Waiting for $distro in the WSL integration")) {
                return Halt 'Docker' "Docker Desktop does not serve $distro yet."
            }
        }
        Passed 'Docker' "Docker Desktop serves $distro"
    }

    # What keeps the distribution from the internet while Windows reaches it, said here where Windows can tell: names it does
    # not resolve, which apt and git would wait on for minutes, and a proxy of the company whose certificate it does not trust
    if (-not (Resolves)) {
        return Halt 'Network' "$distro cannot resolve the names of sites while Windows can, which a VPN often causes." $network
    }
    if ((Https) -eq 'certificate' -and -not (TrustWindowsRoot)) {
        return Halt 'Network' "$distro does not trust the certificate of the sites it reaches." $network
    }

    # Where install.sh clones the lab, by its path: PowerShell 7 makes ~ the folder of Windows before wsl.exe reads it
    $lab = Linux "echo $(if ($env:GRAVITEE_LAB_DIR) { $env:GRAVITEE_LAB_DIR } else { '$HOME/gravitee-lab' })"

    # Port 9000 held on Windows by another program: the browser would find that program in place of the Lab UI, which
    # listens in the distribution and reaches Windows through WSL's relay
    $holder = Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $name = if ($holder) { (Get-Process -Id $holder.OwningProcess -ErrorAction SilentlyContinue).ProcessName }
    if ($name -and $name -notmatch '^wsl') {
        return Halt 'Lab UI' "Port 9000, the Lab UI's, is used by $name on Windows. Stop it, then run this command again."
    }

    # A shortcut that opens the Lab UI next time, in a window named Gravitee Lab, which stays open on an error so that it can
    # be read
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Gravitee Lab.lnk'))
    $link.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $link.Arguments = "/c title Gravitee Lab && wsl.exe -d $distro -- $lab/bin/gravitee-lab || pause"
    $link.IconLocation = (Join-Path $env:SystemRoot 'System32\wsl.exe') + ',0'
    $link.Description = 'Opens the Gravitee Lab UI'
    $link.Save()
    Passed 'Shortcut' 'Gravitee Lab, in the Start menu: it opens the Lab UI next time'

    # The rest is install.sh's, in the distribution: the tools, the clone, the settings, the command gravitee-lab, then the
    # Lab UI in this window. It is read by Windows, which trusts what the company's proxy shows, and run from Windows'
    # temporary folder
    try { $script = Invoke-RestMethod "$raw/install.sh" } catch {
        return Halt 'Gravitee Lab' "install.sh could not be downloaded: $($_.Exception.Message)"
    }
    $file = Join-Path $env:TEMP 'gravitee-lab-install.sh'
    [IO.File]::WriteAllText($file, ($script -replace "`r", ''))
    $path = Linux "wslpath -u '$file'"
    $settings = @()
    if (-not $fancy) { $settings += 'LC_CTYPE=C' }   # install.sh draws its marks as this console can, in plain ones then
    foreach ($name in 'GRAVITEE_LAB_BRANCH', 'GRAVITEE_LAB_DIR', 'GRAVITEE_LAB_REPOSITORY', 'GRAVITEE_LAB_YES', 'GRAVITEE_LAB_NO_START', 'NO_COLOR') {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $settings += "$name=$value" }
    }
    $homeDir = Linux 'echo $HOME'
    & wsl.exe -d $distro --cd $homeDir -- env @settings sh $path
    # A minimal distribution had no client to tell a certificate it does not trust before the clone: it has git now. A lab
    # that is there failed later, or its Lab UI was stopped: install.sh said what it had to
    if ($LASTEXITCODE -ne 0 -and -not (Linux "test -d '$lab/.git' && echo yes") -and (Https) -eq 'certificate' -and (TrustWindowsRoot)) {
        & wsl.exe -d $distro --cd $homeDir -- env @settings sh $path
    }
}
