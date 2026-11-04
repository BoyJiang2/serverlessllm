def _parse_visible_devices() -> Union[list[int], list[str]]:
    r"""Parse CUDA_VISIBLE_DEVICES environment variable."""
    var = os.getenv("CUDA_VISIBLE_DEVICES")

    if torch.version.hip:
        hip_devices = os.getenv("HIP_VISIBLE_DEVICES")
        rocr_devices = os.getenv("ROCR_VISIBLE_DEVICES")

        # You must take care if both HIP and ROCR env vars are set as they have
        # different meanings. Both env vars accept either a list of ints or a
        # list of UUIDs. The ROCR env var is processed first which then reduces
        # the number of GPUs that HIP can select from.
        if rocr_devices is not None:
            rocr_count = len(rocr_devices.split(","))
            if hip_devices is not None:
                # sanity check if both env vars are set
                if len(hip_devices.split(",")) > rocr_count:
                    raise RuntimeError(
                        "HIP_VISIBLE_DEVICES contains more devices than ROCR_VISIBLE_DEVICES"
                    )
                # HIP_VISIBLE_DEVICES is preferred over ROCR_VISIBLE_DEVICES
                var = hip_devices
            else:
                return list(range(rocr_count))
        elif hip_devices is not None:
            var = hip_devices

    if var is None:
        return list(range(64))

    def _strtoul(s: str) -> int:
        """Return -1 or positive integer sequence string starts with."""
        if not s:
            return -1
        for idx, c in enumerate(s):
            if not (c.isdigit() or (idx == 0 and c in "+-")):
                break
            if idx + 1 == len(s):
                idx += 1
        return int(s[:idx]) if idx > 0 else -1

    def parse_list_with_prefix(lst: str, prefix: str) -> list[str]:
        rcs: list[str] = []
        for elem in lst.split(","):
            # Repeated id results in empty set
            if elem in rcs:
                return cast(list[str], [])
            # Anything other but prefix is ignored
            if not elem.startswith(prefix):
                break
            rcs.append(elem)
        return rcs

    if var.startswith("GPU-"):
        return parse_list_with_prefix(var, "GPU-")
    if var.startswith("MIG-"):
        return parse_list_with_prefix(var, "MIG-")
    # CUDA_VISIBLE_DEVICES uses something like strtoul
    # which makes `1gpu2,2ampere` is equivalent to `1,2`
    rc: list[int] = []
    for elem in var.split(","):
        x = _strtoul(elem.strip())
        # Repeated ordinal results in empty set
        if x in rc:
            return cast(list[int], [])
        # Negative value aborts the sequence
        if x < 0:
            break
        rc.append(x)
    return rc