# Native container-scope deadline — source review only

**Verdict: the exact versions support a per-task OCI annotation -> native systemd scope runtime timer, conditional on runc using its systemd cgroup driver. This can close the post-scope-activation container/init/hook gap, but does not by itself bound Docker's scope-external shim/runc or pre-scope startup. No GPU retry is approved.**

No host commands, probes, containers or configuration changes were executed by this review. Local receipts/small snapshots were read; upstream version-pinned primary source was fetched as text. Inputs and SHA256 are preserved in `work/native-scope-deadline-source-inputs.json`.

## Existing evidence and what it does not cover

`outputs/os-guard-cpu-receipt.json` proves a native Type=exec service with RuntimeMaxSec10/TimeoutStopSec3/control-group kill cleaned three TERM-resistant CPU processes in delegated descendants, with13 release samples over60.093s. All those processes were under the tested service. It did not contain a daemon-created Docker sibling scope.

`outputs/scratch-hook-cpu-receipt.json` proves one instrumented CPU createContainer hook executed `--version` and exited0; missing application produced expected create rc1, delete rc0 and60.104s release observation. That was not an injected hung createContainer hook and did not reproduce the original GPU failure. Neither receipt proves this annotation's Docker transport or a deadline on a daemon-created scope.

Root's existing `work/native-scope-host-version.txt` records Docker client/server27.0.3, negotiated API1.46, create-help `--annotation map`, and systemd249 (249.11-0ubuntu3.12). Existing runtime snapshot records runc1.3.5, commit v1.3.5-0-g488fc13e. Versions are sufficient at source level; installed driver selection and resulting unit properties remain facts to verify in a separately authorized CPU test.

## Exact transport and native implementation

1. Docker27.0.3 CLI `opts.go:321–322` registers --annotation and requires API1.43. Its HostConfig construction at:703 preserves annotations. [Pinned CLI source](https://github.com/docker/cli/blob/v27.0.3/cli/command/container/opts.go#L321-L322). Docker's API history documents HostConfig.Annotations starting1.43. [Docker API history](https://docs.docker.com/reference/api/engine/version-history/#v143-api-changes).
2. Moby27.0.3 `daemon/oci_linux.go:1052` passes HostConfig.Annotations to containerd OCI WithAnnotations. This is an OCI annotation, not a Docker label or environment variable. [Pinned daemon source](https://github.com/moby/moby/blob/v27.0.3/daemon/oci_linux.go#L1052).
3. runc1.3.5 `specconv/spec_linux.go:688–719` recognizes org.systemd.property., parses each value as a GVariant and builds systemd properties; :737–742 does so **only when UseSystemdCgroup is true**. [Pinned parser](https://github.com/opencontainers/runc/blob/v1.3.5/libcontainer/specconv/spec_linux.go#L688-L742).
4. Its vendored systemd cgroup-v2 Apply(pid) creates a scope with Slice, Delegate and PIDs, appends custom SystemdProps at:328, then starts the transient unit at:330. They are creation-time properties rather than a post-start race to call set-property. [Pinned Apply](https://github.com/opencontainers/runc/blob/v1.3.5/vendor/github.com/opencontainers/cgroups/systemd/v2.go#L283-L331). Scope naming derives from the actual OCI CgroupsPath, generally docker-<fullCID>.scope for Docker; do not manufacture the identity from the name alone. [runc systemd documentation](https://github.com/opencontainers/runc/blob/v1.3.5/docs/systemd.md#systemd-unit-name-and-placement).
5. systemd249 `dbus-scope.c:49,74–75` exposes and accepts RuntimeMaxUSec as unsigned64-bit type t; :47,71–72 likewise accepts TimeoutStopUSec. :182 accepts the scope's transient kill settings. [Pinned scope D-Bus source](https://github.com/systemd/systemd/blob/v249/src/core/dbus-scope.c#L45-L75), [scope kill settings](https://github.com/systemd/systemd/blob/v249/src/core/dbus-scope.c#L175-L184).

Proposed **per-task properties**, not a command to execute now:

```json
{
  "org.systemd.property.RuntimeMaxUSec": "uint64 20000000",
  "org.systemd.property.TimeoutStopUSec": "uint64 3000000",
  "org.systemd.property.KillMode": "'control-group'",
  "org.systemd.property.SendSIGKILL": "true",
  "org.systemd.property.FinalKillSignal": "int32 9"
}
```

If later approved through CLI, each entire key=value containing spaces must be one quoted --annotation argument. RuntimeMaxUSec uses microseconds, so20,000,000 means20s; GVariant spelling is `uint64 20000000`, not `uint64(20000000)`. No daemon.json change, global daemon service timeout, shared slice modification or third-party unit kill is needed for the leaf scope property.

## Timer semantics and limits

systemd249 scope_start attaches PIDs first, enters SCOPE_RUNNING, then arms active_enter_timestamp.monotonic+runtime_max_usec. [scope.c:371–383](https://github.com/systemd/systemd/blob/v249/src/core/scope.c#L371-L383). Therefore20s is measured from **scope activation**, not Docker CLI invocation, request arrival, image preparation, shim startup, or an earlier blocked StartTransientUnit/DBus/cgroup operation. A scope that never becomes active has not armed this timer.

At expiry, systemd begins stop with timeout result; if TERM/controller stop does not succeed, the stop timer advances to SIGKILL when SendSIGKILL is true. [scope.c:536–562](https://github.com/systemd/systemd/blob/v249/src/core/scope.c#L536-L562). A scope controller may first receive RequestStop instead of direct TERM, but the same stop timer bounds that phase. [scope.c:295–327](https://github.com/systemd/systemd/blob/v249/src/core/scope.c#L295-L327). KillMode/control-group, SendSIGKILL and FinalKillSignal have supported transient setters in249. [dbus-kill.c:61–77](https://github.com/systemd/systemd/blob/v249/src/core/dbus-kill.c#L61-L77).

Thus this is a20s runtime deadline plus approximately3s TERM/controller grace before KILL, scheduling/cleanup/observation time, and potentially another stop-state timer. It is not a guarantee that every process is gone at wall-clock20.000s. Crucially, the SIGKILL-state timeout logs that surviving processes are ignored and marks the scope dead; a timeout/failed/inactive unit alone cannot prove release, especially for uninterruptible kernel tasks. Preserve PID+starttick identity checks, actual recursive cgroup population/removal and the required60-second release observation. Do not weaken stop limits or infer release from Docker status alone.

## Smallest remaining scope-external boundary

A systemd service wrapping `docker start` contains the CLI, not automatically the daemon-created runc or containerd-shim. A deadline on docker-<CID>.scope covers only processes actually placed in that cgroup, normally init/createContainer descendants and the application. It does not kill a parent runc/shim left in another daemon/runtime cgroup. Killing the CLI or cancelling the Docker request is not proof of cancelling startup.

The minimum additional requirement is an independently armed native deadline covering **only this task's actual runtime-launch processes** before they can enter the hazardous stage, plus a known cleanup path if scope creation itself stalls. Reuse the already proved bounded Type=exec/control-group service for direct scratch-runc CPU fault injection, with runc launched inside it; then the new leaf scope timer covers any systemd-created container sibling while the service bounds its own launcher. Do not assume that this changes Docker shim placement.

For the real Docker path, do not claim all-domain closure until a per-CID runtime containment/termination boundary is demonstrated: pin full CID, executable/starttick identity and actual cgroup for the shim/runc; prove deadline action is independent of a hung start API and does not touch shared dockerd/containerd or unrelated tasks. Moving a discovered PID after the hook already runs leaves a race and is insufficient proof of pre-execution coverage. If the existing Docker/containerd integration has no per-task pre-launch scope control, leaf annotations alone leave this blocked; no global daemon change or broad custom watchdog is proposed here.

## Minimal next CPU proof, separately reviewed before execution

The next discriminating stimulus is a **TERM-resistant, CPU-only createContainer hook intentionally held beyond20s**, with the annotation in the real OCI spec, no model/GPU/Store/network and bounded resources. Record that it entered the actual unit/cgroup before the deadline. A plain sleep application checks a different stage and cannot substitute for that evidence. Where a scratch OCI test is used, contain its launcher in the existing native guard service; this proves runc/systemd hook-stage capability, not Docker end-to-end transport.

To claim Docker transport, retain the actual per-CID HostConfig.Annotations and resulting OCI annotations, systemd cgroup driver/config, resulting scope name/ControlGroup/PIDs and RuntimeMaxUSec20s/TimeoutStopUSec3s/kill properties while active. Do not treat a successful create API response as acceptance of downstream systemd settings. The test must show native timeout->TERM/controller->KILL, owned hook/init disappearance, separately bounded runc/shim convergence, precise monotonic times and60s release observation. If a target property is unsupported or any pre-launch/runtime scope is uncovered, stop CPU escalation and preserve failure; no GPU follow-on is automatically granted.

No new probe framework is needed for this source conclusion. The existing CPU scratch hook and native guard mechanisms can supply the bounded stimulus and launcher boundary after a concrete independently reviewed plan. GPU stop/no-retry and all existing failed receipts remain unchanged.
