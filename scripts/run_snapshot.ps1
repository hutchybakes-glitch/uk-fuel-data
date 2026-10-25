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
    # After the PC wakes from sleep the network can take a minute to come back: wait up to 3 minutes for GitHub to answer
    $waited = 0
    # Check every 15 seconds until GitHub answers or 3 minutes have passed
    while (-not (Test-Connection -ComputerName 'github.com' -Count 1 -Quiet) -and $waited -lt 180) { Start-Sleep -Seconds 15; $waited += 15 }
    # Note any wait, so slow wake-ups show in the log
    if ($waited -gt 0) { Write-Log "Waited $waited s for the network" }
    # Get any changes made elsewhere first, so the push cannot be rejected
    git pull --rebase --autostash --quiet 2>&1 | Out-Null
    # Run the snapshot, and once more after a minute if it fails (Fuel Finder is occasionally slow or busy)
    foreach ($attempt in 1, 2) {
        # Run the snapshot and capture everything it prints
        $out = & 'C:\Python313\python.exe' 'scripts\snapshot.py' 2>&1
        # Record what it printed, with the attempt number
        Write-Log ("Attempt $attempt | " + ($out -join ' | '))
        # Stop retrying once it has worked
        if ($LASTEXITCODE -eq 0) { break }
        # Wait a minute before the second attempt
        if ($attempt -eq 1) { Start-Sleep -Seconds 60 }
    }
    # Stop if both attempts failed, leaving the last good files in place
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
