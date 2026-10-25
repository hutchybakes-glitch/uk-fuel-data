# Runs one Fuel Finder snapshot on this PC and pushes the data to GitHub.
# Called by the Windows scheduled task "UK Fuel Snapshot" six times a day (06:40 to 21:40 UK time); the task may wake the PC from sleep (on mains power) to run it.
# Fuel Finder only answers UK IP addresses, so this cannot run on GitHub's own servers.
# Credentials: C:\Users\User\.fuelfinder.env (FUEL_FINDER_CLIENT_ID=..., FUEL_FINDER_CLIENT_SECRET=...).

# Stop on the first error from a PowerShell command
$ErrorActionPreference = 'Stop'
# The repo folder is the parent of this script's folder
$repo = Split-Path -Parent $PSScriptRoot
# Keep a rolling log next to the repo (git-ignored)
$log = Join-Path $repo 'logs\snapshot.log'
# Make sure the log folder exists
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

# Write a time-stamped line to the log
function Write-Log($text) {
    # Prefix the line with the local time
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $text" | Add-Content -Path $log -Encoding utf8
}

# Work from the repo folder
Set-Location $repo
# Note the start
Write-Log 'Start'
try {
    # Get any changes made elsewhere first, so the push cannot be rejected
    git pull --rebase --autostash --quiet 2>&1 | Out-Null
    # Run the snapshot and capture everything it prints
    $out = & 'C:\Python313\python.exe' 'scripts\snapshot.py' 2>&1
    # Record what it printed
    Write-Log ($out -join ' | ')
    # Stop if the snapshot failed, leaving the last good files in place
    if ($LASTEXITCODE -ne 0) { throw "snapshot.py exit code $LASTEXITCODE" }
    # Stage the data folder
    git add data
    # Commit and push only when something changed
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        # Commit with the UTC time in the message
        git commit --quiet -m "Snapshot $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mmZ'))"
        # Push to GitHub using the PC's saved Git credentials
        git push --quiet 2>&1 | Out-Null
        # Stop if the push failed
        if ($LASTEXITCODE -ne 0) { throw "git push exit code $LASTEXITCODE" }
        # Note success
        Write-Log 'Pushed'
    } else {
        # Note that nothing changed
        Write-Log 'No changes'
    }
} catch {
    # Record the error so a failed run is easy to diagnose
    Write-Log "FAILED: $_"
    # Exit with an error so Task Scheduler shows the run as failed
    exit 1
}
