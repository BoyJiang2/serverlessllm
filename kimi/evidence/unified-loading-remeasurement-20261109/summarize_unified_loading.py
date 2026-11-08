"""Postprocess only the new four-case endpoint supplement, after final release."""
import argparse
import csv
import hashlib
import io
import json
import math
import pathlib
import tempfile


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path, missing=False):
    path = pathlib.Path(path)
    if missing and not path.exists():
        return None, None
    require(path.is_absolute() and not path.is_symlink() and path.is_file(), "invalid evidence file")
    before = path.stat()
    require(before.st_size <= 2 << 20, "small evidence bound")
    raw = path.read_bytes(); after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "evidence changed")
    require(len(raw) <= 2 << 20, "small evidence bound")
    return json.loads(raw), {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def recompute(value, run, source, protocol_sha):
    require(value["status"] == "COMPLETE8_RANK_HOST_LOAD_CALL_SPAN" and value["run_id"] == run, "span identity/status")
    require(value["source_commit"] == source and value["protocol_sha256"] == protocol_sha, "span source/protocol mismatch")
    require(value["model_instance_id"] == run + "-life1", "model instance mismatch")
    ranks = value["ranks"]
    require(len(ranks) == 8 and {r["tp_rank"] for r in ranks} == set(range(8)), "exact8 ranks required")
    require(len({r["pid"] for r in ranks}) == len({r["device_uuid"] for r in ranks}) == len({r["host_owner"]["pid"] for r in ranks}) == 8, "duplicate rank identity")
    domains = set()
    for rank in ranks:
        require(rank["schema"] == "kimi-weight-load-host-endpoints-v1" and rank["status"] == "COMPLETE", "rank incomplete")
        require(rank["run_id"] == run and rank["model_instance_id"] == value["model_instance_id"], "rank group mismatch")
        require(type(rank["tp_rank"]) is int and type(rank["pid"]) is int and rank["pid"] > 0, "rank producer mismatch")
        clock = rank["clock_identity"]
        require(not clock.get("errors") and clock["process_startticks"] == rank["host_owner"]["startticks"], "rank owner clock mismatch")
        domain = tuple(clock[k] for k in ("boot_id", "time_namespace", "monotonic_clock_implementation"))
        require(all(isinstance(x, str) and x for x in domain), "missing clock domain")
        domains.add(domain)
        begin, end = rank["begin_ns"], rank["end_ns"]
        require(type(begin) is int and type(end) is int and 0 < begin <= end, "rank endpoints invalid")
        require(rank["host_duration_ns"] == end - begin and rank["host_duration_s"] == (end - begin) / 1e9, "rank duration contradiction")
        require(rank["begin_boundary"] == "before_model_loader_load_model" and rank["end_boundary"] == "after_model_loader_load_model_return_before_optional_runner_setup" and rank["added_cuda_synchronization"] is False, "rank boundary mismatch")
    require(len(domains) == 1 and len({r["loader_type"] for r in ranks}) == 1, "rank domain/loader mismatch")
    begin, end = min(r["begin_ns"] for r in ranks), max(r["end_ns"] for r in ranks)
    span, maximum = end - begin, max(r["end_ns"] - r["begin_ns"] for r in ranks)
    require(value["begin_ns"] == begin and value["end_ns"] == end and value["span_ns"] == value["weight_load_host_span_ns"] == span and value["weight_load_host_span_s"] == span / 1e9, "stored all-rank span contradiction")
    require(value["max_rank_weight_load_host_duration_ns"] == maximum and value["max_rank_weight_load_host_duration_s"] == maximum / 1e9, "stored max-rank duration contradiction")
    return {"min_begin_ns": begin, "max_end_ns": end, "weight_load_host_all_rank_span_ns": span,
            "weight_load_host_all_rank_span_s": span / 1e9, "max_single_rank_host_duration_ns": maximum,
            "max_single_rank_host_duration_s": maximum / 1e9, "ranks": ranks}


def build(protocol_path, protocol_sha, runs_root, block_final):
    protocol, pref = read(protocol_path)
    require(pref["sha256"] == protocol_sha and protocol["schema"] == "kimi-unified-loading-remeasurement-v1", "frozen protocol mismatch")
    attempts = protocol["attempts"]
    require(len(attempts) == 4 and len({a["attempt_id"] for a in attempts}) == len({a["condition"] for a in attempts}) == 4, "new four-case geometry")
    source = protocol["final_deploy_commit"]
    parent, parent_ref = read(block_final)
    require(parent["block_id"] == protocol["unified_loading"]["block_id"] and parent["source_commit"] == source and parent["protocol_sha256"] == protocol_sha, "parent identity mismatch")
    require(parent["status"] in ("FOUR_CONDITION_BLOCK_RELEASE60_PASS", "FAILED_RETAINED_NO_RETRY"), "parent not terminal")
    expected_runs = {a["attempt_id"] for a in attempts}
    require(len({r["run_id"] for r in parent["attempts"]}) == len(parent["attempts"]) and {r["run_id"] for r in parent["attempts"]} <= expected_runs, "parent attempts outside supplement")
    rows = []
    for attempt in attempts:
        run = attempt["attempt_id"]; folder = pathlib.Path(runs_root) / run
        data, refs = {}, {}
        for key, name in (("span", "unified-loading-host-span.json"), ("validity", "sample-validity.json"), ("terminal", "condition-terminal.json"), ("cleanup", "cleanup-result.json"), ("controller", "controller-final.json")):
            data[key], refs[key] = read(folder / name, missing=True)
        if data["controller"] is None:
            data["controller"], refs["controller"] = read(folder / "controller-failed.json", missing=True)
        for key in ("validity", "terminal", "cleanup"):
            if data[key] is not None:
                require(data[key]["run_id"] == run and data[key]["condition"] == attempt["condition"], "attempt receipt identity mismatch")
        valid = (data["validity"] or {}).get("primary_sample_qualified", False)
        require(type(valid) is bool, "qualification not boolean")
        cleanup = data["cleanup"] or {}; quiet = cleanup.get("quiet_seconds")
        normal = data["terminal"] is not None and data["terminal"].get("normal") is True and cleanup.get("normal") is True and type(quiet) in (int, float) and math.isfinite(quiet) and quiet >= 60
        measured = recompute(data["span"], run, source, protocol_sha) if data["span"] is not None else None
        require(not valid or (normal and measured is not None and data["controller"] is not None and data["controller"]["status"].endswith("_PASS")), "qualified sample contradicts cleanup/controller/endpoints")
        parent_rows = [r for r in parent["attempts"] if r["run_id"] == run]
        require(len(parent_rows) <= 1, "duplicate parent run")
        rows.append({"run_id": run, "condition": attempt["condition"], "attempt_qualified": valid,
                     "runtime_normal_cleanup": normal, "parent_attempt_terminal": parent_rows[0]["status"] if parent_rows else "NOT_STARTED",
                     "controller_terminal": (data["controller"] or {}).get("status", "NOT_OBSERVED"),
                     "measurement": measured, "missing_reason": "ENDPOINT_FILE_NOT_OBSERVED" if measured is None else None,
                     "source_commit": source, "producer_overlay_sha256": protocol["unified_loading"]["overlay"]["sha256"], "evidence": refs})
    if parent["status"] == "FOUR_CONDITION_BLOCK_RELEASE60_PASS":
        require(all(r["attempt_qualified"] and r["runtime_normal_cleanup"] for r in rows) and len(parent["attempts"]) == 4 and all(r["returncode"] == 0 and r["status"].endswith("_PASS") for r in parent["attempts"]), "parent PASS contradicts attempts")
    eligible = parent["status"] == "FOUR_CONDITION_BLOCK_RELEASE60_PASS" and all(r["attempt_qualified"] and r["runtime_normal_cleanup"] and r["measurement"] is not None for r in rows)
    summary, observed = [], []
    for row in rows:
        row["eligible_new_complete_block"] = eligible
        values = [row["measurement"]["weight_load_host_all_rank_span_s"]] if eligible else []
        maximum = [row["measurement"]["max_single_rank_host_duration_s"]] if eligible else []
        observed.append({"condition": row["condition"], "attempt_qualified": row["attempt_qualified"], "raw_all_rank_span_s": [row["measurement"]["weight_load_host_all_rank_span_s"]] if row["measurement"] else [], "raw_max_single_rank_duration_s": [row["measurement"]["max_single_rank_host_duration_s"]] if row["measurement"] else [], "scope": "Descriptive observed only; not complete-block primary if parent failed."})
        summary.append({"condition": row["condition"], "qualified_n": len(values), "raw_all_rank_span_s": values,
                        "mean_all_rank_span_s": values[0] if values else None, "median_all_rank_span_s": values[0] if values else None,
                        "raw_max_single_rank_duration_s": maximum, "mean_max_single_rank_duration_s": maximum[0] if maximum else None,
                        "median_max_single_rank_duration_s": maximum[0] if maximum else None})
    return {"schema": "kimi-unified-loading-postprocess-v1", "protocol": pref, "parent_block_terminal": parent["status"],
            "parent_evidence": parent_ref, "rows": rows, "complete_block_n": int(eligible), "complete_block_primary_n1_by_condition": summary, "descriptive_observed": observed,
            "limits": ["New supplemental four cases only; original40/old8paired blocks not merged or replaced.",
                       "Host loader-call envelope, no added CUDA sync; not device completion or pureIO.",
                       "No inference/CI/significance at n1. Unqualified endpoint observations remain raw, not qualified means."]}


def export(report, prefix):
    jp, cp = pathlib.Path(str(prefix) + ".json"), pathlib.Path(str(prefix) + ".csv")
    require(not jp.exists() and not cp.exists(), "exclusive outputs already exist")
    columns = ["run_id", "condition", "attempt_qualified", "runtime_normal_cleanup", "parent_attempt_terminal", "eligible_new_complete_block", "expected_rank", "tp_rank", "pid", "host_pid", "process_startticks", "device_uuid", "model_instance_id", "boot_id", "time_namespace", "monotonic_clock_implementation", "begin_ns", "end_ns", "host_duration_ns", "all_rank_span_ns", "max_rank_duration_ns", "source_commit", "producer_overlay_sha256", "endpoint_evidence_sha256", "missing_reason"]
    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=columns); writer.writeheader()
    for row in report["rows"]:
        measurement = row["measurement"]
        rank_map = {r["tp_rank"]: r for r in measurement["ranks"]} if measurement else {}
        for expected_rank in range(8):
            rank = rank_map.get(expected_rank)
            out = {k: row[k] for k in columns if k in row}
            out["expected_rank"] = expected_rank
            if rank:
                out.update({k: rank[k] for k in ("tp_rank", "pid", "device_uuid", "model_instance_id", "begin_ns", "end_ns", "host_duration_ns")})
                out.update({k: rank["clock_identity"][k] for k in ("process_startticks", "boot_id", "time_namespace", "monotonic_clock_implementation")})
                out.update(host_pid=rank["host_owner"]["pid"], all_rank_span_ns=measurement["weight_load_host_all_rank_span_ns"], max_rank_duration_ns=measurement["max_single_rank_host_duration_ns"], endpoint_evidence_sha256=row["evidence"]["span"]["sha256"])
            writer.writerow(out)
    with jp.open("x") as f: json.dump(report, f, indent=2, ensure_ascii=False, allow_nan=False); f.write("\n")
    with cp.open("x", newline="") as f: f.write(buffer.getvalue())
    return {"json": str(jp), "csv": str(cp)}


def selftest():
    import copy
    ranks = []
    for i in range(8):
        ranks.append(dict(schema="kimi-weight-load-host-endpoints-v1", status="COMPLETE", tp_rank=i, pid=100+i, host_owner={"pid": 200+i, "startticks": 300+i}, device_uuid="GPU-"+str(i), run_id="run", model_instance_id="run-life1", clock_identity={"boot_id": "boot", "time_namespace": "time:[1]", "monotonic_clock_implementation": "CLOCK_MONOTONIC", "process_startticks": 300+i}, begin_ns=100+i*10, end_ns=120+i*11, host_duration_ns=20+i, host_duration_s=(20+i)/1e9, loader_type="Loader", begin_boundary="before_model_loader_load_model", end_boundary="after_model_loader_load_model_return_before_optional_runner_setup", added_cuda_synchronization=False))
    value=dict(status="COMPLETE8_RANK_HOST_LOAD_CALL_SPAN", run_id="run", model_instance_id="run-life1", source_commit="source", protocol_sha256="protocol", ranks=ranks, begin_ns=100, end_ns=197, span_ns=97, weight_load_host_span_ns=97, weight_load_host_span_s=97/1e9, max_rank_weight_load_host_duration_ns=27, max_rank_weight_load_host_duration_s=27/1e9)
    result=recompute(value,"run","source","protocol");require(result["weight_load_host_all_rank_span_ns"]==97 and result["max_single_rank_host_duration_ns"]==27,"distinct estimands selftest")
    for fault in ("rank", "clock", "stored", "owner"):
        bad=copy.deepcopy(value)
        if fault=="rank": bad["ranks"][1]["tp_rank"]=0
        if fault=="clock": bad["ranks"][1]["clock_identity"]["boot_id"]="foreign"
        if fault=="stored": bad["span_ns"]=1
        if fault=="owner": bad["ranks"][1]["host_owner"]["startticks"]=1
        try: recompute(bad,"run","source","protocol")
        except ValueError: pass
        else: raise AssertionError("negative selftest accepted: "+fault)
    with tempfile.TemporaryDirectory(prefix="kimi-endpoint-postprocess-unit-") as folder:
        p=pathlib.Path(folder)/"evidence.json";p.write_text(json.dumps(value));loaded,identity=read(p);require(loaded==value and len(identity["sha256"])==64,"real evidence reader selftest")
        report={"rows":[{"run_id":"run","condition":"synthetic","attempt_qualified":True,"runtime_normal_cleanup":True,"parent_attempt_terminal":"PASS","measurement":result,"missing_reason":None,"source_commit":"source","producer_overlay_sha256":"overlay","evidence":{"span":identity}}]}
        paths=export(report,pathlib.Path(folder)/"summary");require(len(list(csv.DictReader(pathlib.Path(paths["csv"]).open())))==8,"eight CSV rows selftest")
        try: export(report,pathlib.Path(folder)/"summary")
        except ValueError: pass
        else: raise AssertionError("exclusive output selftest")
    with tempfile.TemporaryDirectory(prefix="kimi-endpoint-build-unit-") as folder:
        root=pathlib.Path(folder); protocol_path=root/"protocol.json"; final_path=root/"final.json"
        attempts=[{"attempt_id":"new-"+str(i),"condition":condition} for i,condition in enumerate(("safetensors_disk_hot","store_disk_cold","safetensors_disk_cold","store_pool_hit"))]
        protocol={"schema":"kimi-unified-loading-remeasurement-v1","attempts":attempts,"final_deploy_commit":"source","unified_loading":{"block_id":"new-block","overlay":{"sha256":"overlay"}}}
        protocol_path.write_text(json.dumps(protocol)); ph=hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        final={"block_id":"new-block","source_commit":"source","protocol_sha256":ph,"status":"FOUR_CONDITION_BLOCK_RELEASE60_PASS","attempts":[{"run_id":a["attempt_id"],"status":"CONDITION_PASS","returncode":0} for a in attempts]}
        for attempt in attempts:
            run=attempt["attempt_id"]; d=root/run;d.mkdir(); v=copy.deepcopy(value)
            v.update(run_id=run,model_instance_id=run+"-life1",protocol_sha256=ph)
            for rank in v["ranks"]:rank.update(run_id=run,model_instance_id=run+"-life1")
            for name,data in [("unified-loading-host-span.json",v),("sample-validity.json",{"run_id":run,"condition":attempt["condition"],"primary_sample_qualified":True}),("condition-terminal.json",{"run_id":run,"condition":attempt["condition"],"normal":True}),("cleanup-result.json",{"run_id":run,"condition":attempt["condition"],"normal":True,"quiet_seconds":60}),("controller-final.json",{"status":"CONDITION_PASS"})]: (d/name).write_text(json.dumps(data))
        final_path.write_text(json.dumps(final)); report=build(protocol_path,ph,root,final_path)
        require(report["complete_block_n"]==1 and all(x["qualified_n"]==1 for x in report["complete_block_primary_n1_by_condition"]),"complete block selftest")
        final["status"]="FAILED_RETAINED_NO_RETRY";final_path.write_text(json.dumps(final));report=build(protocol_path,ph,root,final_path)
        require(report["complete_block_n"]==0 and all(x["qualified_n"]==0 and x["mean_all_rank_span_s"] is None for x in report["complete_block_primary_n1_by_condition"]) and all(x["raw_all_rank_span_s"] for x in report["descriptive_observed"]),"failed parent must not create primary means")
        last=root/attempts[-1]["attempt_id"]
        for file in last.iterdir():file.unlink()
        final["attempts"].pop();final_path.write_text(json.dumps(final));report=build(protocol_path,ph,root,final_path)
        paths=export(report,root/"partial-summary");csvrows=list(csv.DictReader(pathlib.Path(paths["csv"]).open()))
        require(len(csvrows)==32 and [r["expected_rank"] for r in csvrows[-8:]]==[str(i) for i in range(8)] and all(r["tp_rank"]=="" and r["missing_reason"]=="ENDPOINT_FILE_NOT_OBSERVED" for r in csvrows[-8:]),"eight missing slots selftest")
        bad=root/attempts[0]["attempt_id"]/'cleanup-result.json';record=json.loads(bad.read_text());record["normal"]=False;bad.write_text(json.dumps(record))
        try: build(protocol_path,ph,root,final_path)
        except ValueError: pass
        else:raise AssertionError("qualification/cleanup contradiction accepted")
    print(json.dumps({"status":"CPU_SYNTHETIC_SELFTEST_PASS","checks":12,"actual_observations_read":0}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test",action="store_true")
    for key in ("protocol","protocol-sha","runs-root","block-final","output-prefix"): parser.add_argument("--"+key)
    args=parser.parse_args()
    if args.self_test: selftest(); return
    require(all(getattr(args,k.replace("-","_")) for k in ("protocol","protocol-sha","runs-root","block-final","output-prefix")),"missing CLI input")
    report=build(args.protocol,args.protocol_sha,args.runs_root,args.block_final)
    report["implementation_sha256"]=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(export(report,args.output_prefix)))


if __name__=="__main__": main()
