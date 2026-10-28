# Gravitee Lab on Windows: everything from a bare Windows to the Lab UI in your browser. The lab runs in WSL 2, in a Linux
# distribution, with Docker Desktop. In PowerShell (no administrator needed: Windows asks when a step needs it):
#
#   irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex
#
# Each run does only what is missing, so run it again after a restart it asks for, or at any time: WSL, a distribution
# (Ubuntu) and its Linux user, the tools the lab needs in it, Docker Desktop serving it, the clone in ~/gravitee-lab, a
# Gravitee Lab shortcut in the Start menu, then the Lab UI. It never deletes anything.
#
# GRAVITEE_LAB_DISTRO names another distribution to use, GRAVITEE_LAB_BRANCH the branch to clone.
# Written for Windows PowerShell 5.1, the one every Windows has, in ASCII: it reads no other encoding without a byte
# order mark.

& {
    # Each step reads the exit code of what it runs: with errors that stop, Windows PowerShell 5.1 stops at the first line a
    # program writes to its error output
    $ErrorActionPreference = 'Continue'
    $env:WSL_UTF8 = '1'   # wsl.exe answers in UTF-16 otherwise
    $repository = 'https://github.com/gravitee-io-labs/gravitee-lab.git'
    $distro = if ($env:GRAVITEE_LAB_DISTRO) { $env:GRAVITEE_LAB_DISTRO } else { 'Ubuntu' }
    $branch = if ($env:GRAVITEE_LAB_BRANCH) { $env:GRAVITEE_LAB_BRANCH } else { 'main' }
    $help = 'https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/installation.md#windows-with-wsl-2'
    $steps = 6

    function Step($number, $text) { Write-Host ''; Write-Host "[$number/$steps] $text" -ForegroundColor Cyan }
    function Say($text) { Write-Host "      $text" }
    function Ok($text) { Write-Host "      $text" -ForegroundColor Green }
    function Halt($text) {
        Write-Host ''
        Write-Host $text -ForegroundColor Yellow
        Write-Host "Help: $help"
    }

    # A script for sh in the distribution, passed in base64: Windows PowerShell 5.1 loses the quotes of an argument it
    # gives a program, and writes CRLF line ends to its input. The default user runs it unless a user is named.
    function Linux([string]$script, [string]$user) {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r", '')))
        $as = if ($user) { @('-u', $user) } else { @() }
        & wsl.exe -d $distro @as -- sh -c "echo $encoded | base64 -d | sh"
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

    Write-Host 'Gravitee Lab for Windows' -ForegroundColor White

    # 1. WSL. `wsl --version` answers only from the WSL of the Store, the one Docker Desktop needs
    Step 1 'WSL 2'
    & wsl.exe --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Say 'Installing WSL. Windows asks for administrator rights.'
        $install = Start-Process wsl.exe -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait -PassThru
        if ($install.ExitCode -ne 0) {
            return Halt 'WSL did not install. Turn on virtualization in the firmware of this computer if WSL says so, then run this command again.'
        }
        return Halt 'WSL is installed. Restart Windows, then run this command again.'
    }
    Ok 'WSL is installed'

    # 2. The distribution
    Step 2 "Linux distribution: $distro"
    $wsl = Distributions
    if ($wsl.Names -notcontains $distro) {
        Say "Installing $distro"
        & wsl.exe --install -d $distro --no-launch
        if ($LASTEXITCODE -ne 0) { return Halt "$distro did not install." }
        $wsl = Distributions
    }
    if (-not ((Linux 'uname -r') -match 'WSL2')) {
        Say "Moving $distro to WSL 2, which Docker Desktop needs. This takes a few minutes."
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
        & wsl.exe --terminate $distro | Out-Null   # its default user is read when it starts: before Docker Desktop serves it
    }
    Ok "$distro, user $user"

    # 3. The tools of the lab in the distribution, installed as root: no password to type
    Step 3 'Git, Make, Python and OpenSSL'
    $missing = Linux 'for tool in git make python3 openssl; do command -v $tool >/dev/null || printf "%s " $tool; done'
    if ($missing) {
        if (-not (Linux 'command -v apt-get' 'root')) { return Halt "Install $missing in $distro, then run this command again." }
        Say "Installing $missing"
        Linux 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq git make python3 openssl ca-certificates >/dev/null' 'root'
        if ($LASTEXITCODE -ne 0) { return Halt "The tools did not install in $distro. Is the internet reachable from it?" }
    }
    Ok 'All there'

    # 4. Docker Desktop, running and serving the distribution
    Step 4 'Docker Desktop'
    $cli = DesktopCli
    if (-not $cli) {
        Say 'Docker Desktop is not installed. At work, your company may need a Docker subscription, and your IT may install it for you.'
        if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
            return Halt 'Install Docker Desktop (https://docs.docker.com/desktop/setup/install/windows-install/), then run this command again.'
        }
        if ((Read-Host 'Install Docker Desktop now with winget? Windows asks for administrator rights [Y/n]') -match '^[nN]') {
            return Halt 'Install Docker Desktop, then run this command again.'
        }
        & winget.exe install --exact --id Docker.DockerDesktop --source winget
        $cli = DesktopCli
        if (-not $cli) { return Halt 'Docker Desktop did not install.' }
        Say 'If Windows asks you to sign out to finish the install of Docker Desktop, do it, then run this command again.'
    }
    if (-not (WindowsDocker $cli)) {
        Say 'Starting Docker Desktop. The first time, accept its agreement in its window.'
        Start-Process (DesktopApp $cli)
        if (-not (WaitFor { WindowsDocker $cli } 600)) { return Halt 'Docker Desktop did not start. Open it, then run this command again.' }
    }
    if (-not (LinuxDocker)) {
        $wsl = Distributions
        if (-not $wsl.Default -or $wsl.Default -like 'docker-desktop*') {
            # Docker Desktop serves the default distribution, and its own is no distribution to work in
            & wsl.exe --set-default $distro | Out-Null
            $served = WaitFor { LinuxDocker } 30
        } else {
            $served = $false
        }
        if (-not $served) {
            Say "Reconnecting Docker Desktop to $distro"
            & $cli desktop restart *> $null
            $served = WaitFor { LinuxDocker } 120
        }
        if (-not $served) {
            Say "In Docker Desktop, open Settings > Resources > WSL integration, turn on $distro, then Apply. Waiting for it..."
            Start-Process (DesktopApp $cli)
            if (-not (WaitFor { LinuxDocker } 900)) { return Halt "Docker Desktop does not serve $distro yet." }
        }
    }
    Ok "Docker Desktop serves $distro"

    # 5. The clone, in the distribution's own disk: the containers read a folder of Windows slowly
    Step 5 'Gravitee Lab in ~/gravitee-lab'
    if (Linux 'test -d ~/gravitee-lab/.git && echo yes') {
        Ok 'Already there'
    } else {
        Linux "git clone --quiet --branch $branch $repository ~/gravitee-lab"
        if ($LASTEXITCODE -ne 0) {
            return Halt 'The clone failed. Behind a proxy that inspects HTTPS, Linux needs the certificate of your company: see Corporate proxy in docs/troubleshooting.md.'
        }
        Ok 'Cloned'
    }

    # 6. A shortcut that starts the Lab UI, next time
    Step 6 'Shortcut'
    $shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Gravitee Lab.lnk'
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $link.TargetPath = Join-Path $env:SystemRoot 'System32\wsl.exe'
    $link.Arguments = "-d $distro --cd ~ -- make --no-print-directory -C gravitee-lab ui"
    $link.Description = 'Opens the Gravitee Lab UI'
    $link.Save()
    Ok 'Gravitee Lab is in the Start menu: it opens the Lab UI next time'

    Write-Host ''
    Write-Host 'Opening the Lab UI in your browser. Keep this window open while you use it: Ctrl+C stops the Lab UI, and the containers keep running.' -ForegroundColor Green
    & wsl.exe -d $distro --cd ~ -- make --no-print-directory -C gravitee-lab ui
}
