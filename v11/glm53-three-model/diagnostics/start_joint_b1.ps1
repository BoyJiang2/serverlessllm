param(
    [Parameter(Mandatory)][ValidateSet('S1','S2','S3')][string]$Sequence,
    [Parameter(Mandatory)][ValidateSet('r2','r3')][string]$Repeat,
    [string[]]$B2Results,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$node = 'qjhs003'
$control = '/data/brick0/boyjiang/glm53-store-bridge.ZY0JHwah/formal-b1-control-20261119'
$root = '/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118'
$export = "$root/runs/glm53-store-export-20261118T121335Z/result.json"
$slot = @{
    r2 = @{Id='bk-20261120T0341-f8d5e83a'; Start='2026-11-21T05:30:00+08:00'; End='2026-11-21T10:15:00+08:00'}
    r3 = @{Id='bk-20261120T0341-66ed776b'; Start='2026-11-21T10:15:00+08:00'; End='2026-11-21T15:00:00+08:00'}
}[$Repeat]

$snapshot = python C:/Users/23520/.codex/skills/gputime-booking-agent/scripts/gputime_agent.py `
    --token-file C:/Users/23520/.config/gputime-booking-agent/sllm-extension-token snapshot
if ($LASTEXITCODE -ne 0) { throw 'Booking API unavailable' }
$booking = @(($snapshot | ConvertFrom-Json).bookings | Where-Object {
    $_.resource_id -eq 'h20/qjhs003' -and $_.id -eq $slot.Id -and $_.user -eq 'boyjiang'
})
if ($booking.Count -ne 1 -or
    $booking[0].start_ms -ne [DateTimeOffset]::Parse($slot.Start).ToUnixTimeMilliseconds() -or
    $booking[0].end_ms -ne [DateTimeOffset]::Parse($slot.End).ToUnixTimeMilliseconds()) {
    throw "Frozen booking missing or changed for $Repeat"
}

$probe = ssh -o BatchMode=yes -o ConnectTimeout=8 $node "cd '$control' && sha256sum run_three_model_b1.py '$export' && python3 -B -c 'import json; from run_three_model_b1 import resources; print(json.dumps(resources()))' && date +%s%N"
if ($LASTEXITCODE -ne 0 -or $probe.Count -ne 4 -or $probe[3] -notmatch '^[0-9]{19}$') {
    throw 'Could not read frozen B1 source and live resources'
}
if (($probe[0] -split '\s+', 2)[0] -cne 'aec94e1a65be40dd821f0e0f19b1e57676b7b703da19adfec642b8d4d507d6e8' -or
    ($probe[1] -split '\s+', 2)[0] -cne '1e6218953eab706ccdf9b2bfd42eea84ee290db83f0666e0b1e40d311f684547') {
    throw 'Frozen B1 runner or export receipt changed'
}
$resources = $probe[2] | ConvertFrom-Json
$now = [long]$probe[3]
$active = $booking[0].start_ms * 1000000 -le $now -and $now -lt $booking[0].end_ms * 1000000
$idle = $resources.device_mib.Count -eq 8 -and
    ($resources.device_mib -join ',') -eq '0,0,0,0,0,0,0,0' -and
    !$resources.compute_pids -and !$resources.occupied_ports
$enoughMemory = $resources.required_bytes -eq 1871GB -and $resources.available_bytes -ge $resources.required_bytes

if ($PreflightOnly) {
    [pscustomobject]@{Repeat=$Repeat; Sequence=$Sequence; Booking=$slot.Id; Active=$active; Idle=$idle
        AvailableGiB=[Math]::Round($resources.available_bytes / 1GB, 3); RequiredGiB=$resources.required_bytes / 1GB
        MemoryAdmitted=$enoughMemory; SourceAndExportVerified=$true} | ConvertTo-Json -Compress
    return
}

if ($B2Results.Count -ne 3) { throw 'Three B2 recomputation files are required before B1' }
$summaries = @($B2Results | ForEach-Object { Get-Content -LiteralPath $_ -Raw | ConvertFrom-Json })
if ($summaries.Count -ne 3 -or ((@($summaries.sequence | Sort-Object) -join ',') -cne 'S1,S2,S3') -or
    ($summaries | Where-Object {
        $_.qualified -ne $true -or $_.status -ne 'qualified' -or $_.evidence_errors -or
        $_.weight_store_gib -ne 0 -or $_.host_kv_pool_gib -ne 813 -or
        $_.segments.Count -ne 7 -or $_.gpu_release.seconds -lt 60 -or
        !$_.host_monitor -or !$_.kimi_trace
    })) {
    throw 'Three qualified 813 GiB no-Store B2 recomputations are required before B1'
}
if (!$active -or !$idle -or !$enoughMemory) { throw 'Live B1 reservation, GPU, port or RAM admission failed' }

$stamp = [DateTimeOffset]::FromUnixTimeMilliseconds([long][Math]::Floor($now/1e6)).UtcDateTime.ToString('yyyyMMddTHHmmssZ')
$run = "B1-GLM53-$Sequence-$stamp"
$evidence = "$control/launch-$Repeat"
$admission = "$evidence/$run-admission.json"
$tmp = New-TemporaryFile
try {
    ssh -o BatchMode=yes -o ConnectTimeout=8 $node "mkdir -p '$evidence' && test ! -e '$root/formal-b1-runs/$run' && test ! -e '$admission'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare a unique B1 launch' }
    [IO.File]::WriteAllText($tmp.FullName, (@{booking=$booking[0];checked_at_wall_ns=$now} | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    scp -q -o BatchMode=yes -o ConnectTimeout=8 $tmp.FullName "${node}:$admission"
    if ($LASTEXITCODE -ne 0) { throw 'Could not upload fresh B1 admission' }
} finally {
    Remove-Item -LiteralPath $tmp.FullName
}

$payload = @'
set -euC
cd "$control"
nohup bash -c '
    python3 -Bu run_three_model_b1.py --sequence "$sequence" --output "$root/formal-b1-runs/$run" \
      --export-receipt "$export" --admission "$admission" --booking-id "$booking"
    code=$?
    printf "%s\n" "$code" > "$evidence/$run.exit"
    exit "$code"
' > "$evidence/controller-$run.log" 2>&1 < /dev/null &
printf 'controller_pid=%s\n' "$!"
'@
$payload | ssh -o BatchMode=yes -o ConnectTimeout=8 $node "export control='$control' root='$root' export='$export' sequence='$Sequence' run='$run' admission='$admission' booking='$($slot.Id)' evidence='$evidence'; tr -d '\015' | bash -s"
if ($LASTEXITCODE -ne 0) { throw 'B1 launch status uncertain, inspect process and launch evidence' }
Write-Output "run_id=$run root=$root/formal-b1-runs/$run evidence=$evidence"
