# Gravitee Lab on Windows: everything from a bare Windows to the Lab UI in your browser. The lab runs in WSL 2, in a Linux
# distribution, with Docker. In PowerShell (no administrator needed: Windows asks when a step needs it):
#
#   irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex
#
# It uses what the laptop has and adds only what is missing: WSL, a distribution (an Ubuntu or a Debian already there,
# else Ubuntu) and its Linux user, the tools the lab needs in it, a Docker that answers in it (Docker Desktop when there is
# none), the clone in ~/gravitee-lab, a Gravitee Lab shortcut in the Start menu, then the Lab UI. It asks before it changes
# what was there (a distribution of WSL 1, a Docker Desktop that runs containers, the certificates the distribution trusts),
# and never deletes anything. What keeps it from going on (a VPN, a proxy that inspects HTTPS, a port taken on Windows) it
# says in a line. Run it again at any time, also after a restart it asks for.
#
# GRAVITEE_LAB_DISTRO names the distribution to use, GRAVITEE_LAB_BRANCH the branch to clone.
# Written for Windows PowerShell 5.1, the one every Windows has, in ASCII: it reads no other encoding without a byte
# order mark.

& {
    # Each step reads the exit code of what it runs: with errors that stop, Windows PowerShell 5.1 stops at the first line a
    # program writes to its error output
    $ErrorActionPreference = 'Continue'
    $env:WSL_UTF8 = '1'   # wsl.exe answers in UTF-16 otherwise
    $repository = 'https://github.com/gravitee-io-labs/gravitee-lab.git'
    $branch = if ($env:GRAVITEE_LAB_BRANCH) { $env:GRAVITEE_LAB_BRANCH } else { 'main' }
    $help = 'https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/installation.md#windows-with-wsl-2'
    $network = 'https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/troubleshooting.md#windows-and-wsl-2'
    $command = 'irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex'
    $steps = 6

    function Step($number, $text) { Write-Host ''; Write-Host "[$number/$steps] $text" -ForegroundColor Cyan }
    function Say($text) { Write-Host "      $text" }
    function Ok($text) { Write-Host "      $text" -ForegroundColor Green }
    function Halt($text) {
        Write-Host ''
        Write-Host $text -ForegroundColor Yellow
        Write-Host "Help: $help"
    }
    function Yes($question) { (Read-Host "      $question [y/N]") -match '^[yY]' }
    # A stop for a step the person finishes outside, a restart or a sign-out: the command to run next, in the clipboard too
    function Again($text) {
        Halt $text
        try { Set-Clipboard -Value $command; Write-Host "Then run: $command   (in your clipboard)" } catch { Write-Host "Then run: $command" }
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
    # these, else Ubuntu, which is installed. The lab installs its tools with apt.
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

    # Whether a command answers within the time given, asked every few seconds
    function WaitFor([scriptblock]$answers, [int]$seconds) {
        $until = (Get-Date).AddSeconds($seconds)
        while ((Get-Date) -lt $until) {
            if (& $answers) { return $true }
            Start-Sleep -Seconds 3
        }
        return $false
    }

    function LinuxDocker { Linux 'docker info >/dev/null 2>&1' | Out-Null; return $LASTEXITCODE -eq 0 }

    function WindowsDocker($cli) { & $cli info *> $null; return $LASTEXITCODE -eq 0 }

    # Whether the distribution resolves the names of sites: without, apt and git wait minutes before they fail
    function Resolves { [bool](Linux 'timeout 20 getent hosts github.com >/dev/null && echo yes') }
    $noNames = "cannot resolve the names of sites while Windows can, which a VPN often causes."

    # Why the distribution does not reach a site that Windows reaches, from what git or apt said: names it cannot resolve
    # (a VPN often), the certificate of a proxy that inspects HTTPS, which it does not trust, or the network
    function Unreachable([string]$said) {
        if (-not (Resolves)) { return "$distro $noNames" }
        if ($said -match 'certificate|SSL') { return "$distro does not trust the certificate of the sites it reaches." }
        return "$distro cannot reach the internet. Behind a proxy, WSL needs it too."
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
        Say "A proxy of your company inspects HTTPS with the certificate $($root.GetNameInfo('SimpleName', $false)). Windows trusts it, $distro does not."
        if (-not (Yes "Add it to the certificates $distro trusts?")) { return $false }
        $pem = "-----BEGIN CERTIFICATE-----`n" + ([Convert]::ToBase64String($root.RawData, 'InsertLineBreaks') -replace "`r", '') + "`n-----END CERTIFICATE-----`n"
        Linux ("command -v update-ca-certificates >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates >/dev/null; " +
            "mkdir -p /usr/local/share/ca-certificates && printf '%s' '$pem' > /usr/local/share/ca-certificates/windows-$($root.Thumbprint).crt && " +
            'update-ca-certificates >/dev/null 2>&1') 'root' | Out-Null
        return $LASTEXITCODE -eq 0
    }

    Write-Host 'Gravitee Lab for Windows' -ForegroundColor White
    Write-Host 'It uses what this computer has, adds what is missing, and asks before it changes anything you set up.'

    # WSL, a distribution, Docker Desktop and the images of the platform take about 25 GB of the disk of Windows
    $free = [math]::Floor((Get-PSDrive $env:SystemDrive.TrimEnd(':')).Free / 1GB)
    if ($free -lt 25) {
        Write-Host ''
        Say "$env:SystemDrive has $free GB free, and the lab takes about 25 GB with the images of the platform."
        if (-not (Yes 'Go on anyway?')) { return Halt 'Free some space, then run this command again.' }
    }

    # 1. WSL. `wsl --version` answers only from the WSL of the Store, the one Docker Desktop needs
    Step 1 'WSL 2'
    & wsl.exe --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Say 'Installing WSL. Windows asks for administrator rights.'
        $install = Start-Process wsl.exe -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait -PassThru
        if ($install.ExitCode -ne 0) {
            return Halt 'WSL did not install. Turn on virtualization in the firmware of this computer if WSL says so, then run this command again.'
        }
        return Again 'WSL is installed. Restart Windows.'
    }
    Ok 'WSL is installed'

    # 2. The distribution
    $wsl = Distributions
    $distro = Choose $wsl
    Step 2 "Linux distribution: $distro"
    if ($wsl.Names -notcontains $distro) {
        Say "Installing $distro"
        & wsl.exe --install -d $distro --no-launch
        if ($LASTEXITCODE -ne 0) { return Halt "$distro did not install." }
    }
    if (-not ((Linux 'uname -r') -match 'WSL2')) {
        Say "$distro runs on WSL 1, and Docker needs WSL 2. Its files stay, and the move takes a few minutes."
        if (-not (Yes "Move $distro to WSL 2?")) {
            return Halt "Move $distro to WSL 2 (wsl --set-version $distro 2), or name another distribution in GRAVITEE_LAB_DISTRO, then run this command again."
        }
        & wsl.exe --set-version $distro 2
        if ($LASTEXITCODE -ne 0) { return Halt "$distro could not move to WSL 2." }
    }
    $user = Linux 'getent passwd | awk -F: ''$3 >= 1000 && $3 < 65534 { print $1; exit }''' 'root'
    if (-not $user) {
        Say "$distro asks for a user name and a password for Linux. They are not your Windows ones: sudo asks for this password."
        if (Linux 'test -x /usr/lib/wsl/wsl-setup && echo yes' 'root') {
            & wsl.exe -d $distro -u root -- /usr/lib/wsl/wsl-setup
        } else {
            $name = Read-Host 'Linux user name'
            & wsl.exe -d $distro -u root -- adduser $name
            Linux "usermod -aG sudo $name && printf '\n[user]\ndefault=$name\n' >> /etc/wsl.conf" 'root'
        }
        $user = Linux 'getent passwd | awk -F: ''$3 >= 1000 && $3 < 65534 { print $1; exit }''' 'root'
        if (-not $user) { return Halt "$distro has no Linux user yet. Run this command again to create one." }
    }
    # The user that /etc/wsl.conf names is the default one only once the distribution restarts, which would make Docker
    # Desktop lose it: WSL is told now, and an older WSL that cannot be is restarted. A default user set otherwise is kept.
    $named = Linux 'sed -n ''/^\[user\]/,/^\[/s/^[[:space:]]*default[[:space:]]*=[[:space:]]*//p'' /etc/wsl.conf 2>/dev/null' 'root'
    if ($named -and (Linux 'id -un') -ne $named) {
        & wsl.exe --manage $distro --set-default-user $named | Out-Null
        if ($LASTEXITCODE -ne 0) { & wsl.exe --terminate $distro | Out-Null }
    }
    Ok "$distro, user $(Linux 'id -un')"

    # 3. The tools of the lab in the distribution, installed as root: no password to type
    Step 3 'Git, Make, Python and OpenSSL'
    $missing = Linux 'for tool in git make python3 openssl; do command -v $tool >/dev/null || printf "%s " $tool; done'
    if ($missing) {
        if (-not (Linux 'command -v apt-get' 'root')) { return Halt "Install $missing in $distro, then run this command again." }
        if (-not (Resolves)) { return Halt "$distro $noNames See $network" }
        Say "Installing $missing"
        $said = Linux "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq 2>&1 && apt-get install -y -qq $missing 2>&1 >/dev/null" 'root'
        if ($LASTEXITCODE -ne 0) {
            return Halt "The tools did not install. $(Unreachable ($said -join ' ')) See $network"
        }
    }
    Ok 'All there'

    # 4. A Docker that answers in the distribution: the one there (Docker Desktop, Docker Engine in it...), else Docker
    # Desktop, started and serving it
    Step 4 'Docker'
    if (-not (LinuxDocker)) {
        $cli = DesktopCli
        if (-not $cli -and (Linux 'command -v dockerd')) {
            return Halt "Docker Engine is installed in $distro and does not answer: start it (sudo service docker start), then run this command again."
        }
        if (-not $cli) {
            Say 'Docker Desktop is not installed. At work, your company may need a Docker subscription, and your IT may install it for you.'
            if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
                return Halt 'Install Docker Desktop (https://docs.docker.com/desktop/setup/install/windows-install/), then run this command again.'
            }
            if (-not (Yes 'Install Docker Desktop now with winget? Windows asks for administrator rights')) {
                return Halt 'Install Docker Desktop, then run this command again.'
            }
            & winget.exe install --exact --id Docker.DockerDesktop --source winget
            $cli = DesktopCli
            if (-not $cli) { return Halt 'Docker Desktop did not install.' }
            if (-not (whoami /groups | Select-String -SimpleMatch 'docker-users' -Quiet) -and (Get-LocalGroup docker-users -ErrorAction SilentlyContinue)) {
                return Again 'Docker Desktop is installed. Sign out of Windows and in again, so that it lets you use it.'
            }
        }
        if (-not (WindowsDocker $cli)) {
            Say 'Starting Docker Desktop. The first time, accept its agreement in its window.'
            Start-Process (DesktopApp $cli)
            if (-not (WaitFor { WindowsDocker $cli } 600)) { return Halt 'Docker Desktop did not start. Open it, then run this command again.' }
        }
        $served = WaitFor { LinuxDocker } 30
        $wsl = Distributions
        if (-not $served -and (-not $wsl.Default -or $wsl.Default -like 'docker-desktop*')) {
            # Docker Desktop serves the default distribution, and its own is no distribution to work in
            Say "$distro becomes the default distribution of WSL, the one Docker Desktop serves"
            & wsl.exe --set-default $distro | Out-Null
            $wsl = Distributions
            $served = WaitFor { LinuxDocker } 30
        }
        # It served the distribution and lost it (a link of its integration is left), which it does each time the distribution
        # restarts, or it serves the default one and has not seen it yet: a restart of Docker Desktop serves it again
        if (-not $served -and ($wsl.Default -eq $distro -or (Linux 'test -L /usr/bin/docker && echo yes'))) {
            $running = @(& $cli ps -q 2>$null).Count
            if ($running) {
                Say "Docker Desktop must restart to reach $distro, which stops the $running containers that run now."
                if (-not (Yes 'Restart Docker Desktop now?')) {
                    return Halt 'Restart Docker Desktop when you can, then run this command again.'
                }
            } else {
                Say "Restarting Docker Desktop to reach $distro"
            }
            & $cli desktop restart *> $null
            $served = WaitFor { LinuxDocker } 120
        }
        if (-not $served) {
            Say "In Docker Desktop, open Settings > Resources > WSL integration, turn on $distro, then Apply. Waiting for it..."
            Start-Process (DesktopApp $cli)
            if (-not (WaitFor { LinuxDocker } 900)) { return Halt "Docker Desktop does not serve $distro yet." }
        }
    }
    Ok "Docker answers in $distro"

    # 5. The clone, in the distribution's own disk: the containers read a folder of Windows slowly
    Step 5 'Gravitee Lab in ~/gravitee-lab'
    if (Linux 'git -C ~/gravitee-lab remote get-url origin 2>/dev/null | grep -q gravitee-lab && echo yes') {
        Ok 'Already there'
    } elseif (Linux 'test -e ~/gravitee-lab && echo yes') {
        return Halt "~/gravitee-lab in $distro holds something else. Move it, then run this command again."
    } else {
        $clone = "git clone --quiet --branch $branch $repository ~/gravitee-lab 2>&1"
        $said = Linux $clone
        if ($LASTEXITCODE -ne 0 -and ($said -join ' ') -match 'certificate|SSL' -and (TrustWindowsRoot)) { $said = Linux $clone }
        if ($LASTEXITCODE -ne 0) { return Halt "The clone failed. $(Unreachable ($said -join ' ')) See $network" }
        Ok 'Cloned'
    }
    # Its path, not ~: PowerShell 7 makes ~ the folder of Windows before wsl.exe reads it
    $lab = Linux 'cd ~/gravitee-lab && pwd'

    # 6. A shortcut that starts the Lab UI, next time
    Step 6 'Shortcut'
    $shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Gravitee Lab.lnk'
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    # In a window named Gravitee Lab, which stays open on an error so that it can be read
    $link.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $link.Arguments = "/c title Gravitee Lab && wsl.exe -d $distro --cd $lab -- make ui || pause"
    $link.IconLocation = (Join-Path $env:SystemRoot 'System32\wsl.exe') + ',0'
    $link.Description = 'Opens the Gravitee Lab UI'
    $link.Save()
    Ok 'Gravitee Lab is in the Start menu: it opens the Lab UI next time'

    # Port 9000 held on Windows by another program: the browser would find that program in place of the Lab UI, which
    # listens in the distribution and reaches Windows through WSL's relay
    $holder = Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $name = if ($holder) { (Get-Process -Id $holder.OwningProcess -ErrorAction SilentlyContinue).ProcessName }
    if ($name -and $name -notmatch '^wsl') {
        return Halt "Port 9000, the Lab UI's, is used by $name on Windows. Stop it, then run this command again."
    }

    Write-Host ''
    Write-Host 'Opening the Lab UI in your browser. Keep this window open while you use it: Ctrl+C stops the Lab UI, and the containers keep running.' -ForegroundColor Green
    & wsl.exe -d $distro --cd $lab -- make ui
}
