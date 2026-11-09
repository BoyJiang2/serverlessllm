"""Derive this single supplemental block from pinned small exported evidence only."""
import argparse,csv,hashlib,json,math,pathlib,statistics,sys
ORDER=("safetensors_disk_cold","safetensors_disk_hot","store_disk_cold","store_pool_hit")
STAGES=("full","weight_ingestion","postprocessing")
DOMAIN=("boot_id","time_namespace","monotonic_clock_implementation")
BLOCK="kimi-load-stages-20261109-qjhs000-v1-b01"
EXPECTED_MANIFEST="1cbfd7dd17748402c0d98c6b34cc65047fc4108915db779d617347c0b38a3015"

def sha(data):return hashlib.sha256(data).hexdigest()
def duration(begin,end):
    if begin is None or end is None:return None
    assert type(begin) is int and type(end) is int and 0<begin<=end
    return (end-begin)/1e9
def same_domain(left,right):
    assert not left.get("errors") and not right.get("errors")
    assert all(isinstance(left.get(k),str) and left[k]==right[k] for k in DOMAIN)
def write_json(path,value):
    with path.open("x",encoding="utf-8") as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write("\n")
def write_csv(path,rows):
    with path.open("x",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir",type=pathlib.Path);parser.add_argument("output_dir",type=pathlib.Path)
    args=parser.parse_args();root=args.input_dir.resolve();out=args.output_dir
    raw=(root/"EXPORT-MANIFEST.json").read_bytes();assert sha(raw)==EXPECTED_MANIFEST
    manifest=json.loads(raw);assert len(manifest)==284
    data={};refs={}
    for row in manifest:
        rel=row["export_path"];p=root/rel
        assert p.resolve().is_relative_to(root) and p.is_file() and not p.is_symlink() and rel not in data
        value=p.read_bytes();assert len(value)==row["bytes"] and sha(value)==row["sha256"],rel
        data[rel]=value;refs[rel]={"export_path":str(p),"export_sha256":row["sha256"],"bytes":row["bytes"],"original_source_path":row["source_path"],"kind":row["kind"]}
    def read(rel,optional=False):
        if optional and rel not in data:return None
        return json.loads(data[rel])
    block=read("block-final.json");assert block["block_id"]==BLOCK
    assert block["status"]=="THREE_STAGE_EXIT300_BLOCK_RELEASE60_PASS" and block["launch_requested_count"]==4
    release=read("post-release-verification.json")
    assert release["status"]=="ALL_FOUR_NORMAL_EXIT_RELEASE60_AND_GPU_FREE_VERIFIED"
    attempts=[];ranks=[];rankcsv=[];attemptcsv=[]
    for condition in ORDER:
        prefix=condition+"/";run=BLOCK+"-"+condition
        final=read(prefix+"controller-final.json");valid=read(prefix+"sample-validity.json")
        cleanup=read(prefix+"cleanup-result.json");terminal=read(prefix+"condition-terminal.json")
        assert final["condition"]==condition and final["source_commit"]==block["source_commit"] and final["protocol_sha256"]==block["protocol_sha256"]
        assert valid["run_id"]==run and valid["primary_sample_qualified"] is True and valid["invalid_reasons"]==[]
        assert all(valid["gates"][k]["status"]=="PASS" for k in ("cache","gold","clock","ownership"))
        assert cleanup["normal"] is True and cleanup["quiet_seconds"]>=60 and terminal["normal"] is True
        aggregate=read(prefix+"unified-loading-host-span.json")
        assert aggregate["run_id"]==run and aggregate["stage_schema"]=="kimi-weight-load-host-stages-v1"
        before=read(prefix+"load-evidence-original-files-before.json");after=read(prefix+"load-evidence-original-files-after.json")
        assert after["all8_entire_original_hashes_unchanged"] is True and before["files"]==after["files"]
        originals={row["tp_rank"]:row for row in before["files"]};assert set(originals)==set(range(8))
        ownership=read(prefix+"ownership-capsule.json")
        prepbegin=read(prefix+"event-target_condition_construction_begin.json",True)
        prepend=read(prefix+"event-cache_pool_admission_complete.json",True)
        rootclock=prepbegin["clock_identity"] if prepbegin else prepend["clock_identity"]
        measured=read(prefix+"sample-metrics.json",True)
        request={row["case"]:row for row in measured["requests"]} if measured else {}
        arithmetic=request.get("arithmetic");times=arithmetic["producer_times"] if arithmetic else {}
        if arithmetic:same_domain(arithmetic["clock_identity"],rootclock)
        spans={name:[] for name in STAGES};case_ranks=[]
        for rank in range(8):
            rel=prefix+"rank-endpoint-capsules/rank-"+str(rank)+".json";cap=read(rel)
            full=cap["weight_load_host_span"];original=originals[rank]
            assert cap["whole_file_sha_matches_before_after"] is True and cap["load_completed"] is True
            assert (cap["original_path"],cap["original_sha256"],cap["original_bytes"])==(original["path"],original["sha256"],original["bytes"])
            assert cap["tp_rank"]==full["tp_rank"]==rank and cap["pid"]==full["pid"]==original["pid"]
            assert full["status"]=="COMPLETE" and full["schema"]=="kimi-weight-load-host-endpoints-v1" and full["added_cuda_synchronization"] is False
            assert full["stage_schema"]=="kimi-weight-load-host-stages-v1" and set(full["stages"])=={"weight_ingestion","postprocessing"}
            assert full["run_id"]==run and full["model_instance_id"]==run+"-life1"
            same_domain(full["clock_identity"],rootclock);owner=cap["host_owner"]
            assert full["clock_identity"]["process_startticks"]==owner["startticks"]
            assert owner==ownership["owners"][rank] and str(owner["pid"]) in ownership["physical"][cap["device_uuid"]]["processes"]
            assert full["clock_identity"]["container_hostname"]==final["cids"]["worker"][:12]
            assert owner["cgroup"]=="0::/system.slice/docker-"+final["cids"]["worker"]+".scope"
            expected=aggregate["ranks"][rank]
            for key,value in full.items():
                if key=="stages":
                    for phase,row in value.items():
                        assert all(expected["stages"][phase][k]==v for k,v in row.items())
                else:assert expected[key]==value,(condition,rank,key)
            bounds=(("before_base_load_weights","after_base_load_weights_return"),("before_online_quant_condition","after_process_weights_after_loading_return")) if condition.startswith("safetensors") else (("before_store_load_dict","after_store_data_bindings_and_buffer_moves"),("before_store_runtime_hook_traversal","after_store_attention_postprocessing_return"))
            assert full["begin_boundary"]=="before_model_loader_load_model" and full["end_boundary"]=="after_model_loader_load_model_return_before_optional_runner_setup"
            stages={"full":{k:full[k] for k in ("status","begin_ns","end_ns","begin_boundary","end_boundary")},**full["stages"]}
            previous=full["begin_ns"]
            for name,bound in zip(("weight_ingestion","postprocessing"),bounds):
                stage=stages[name];assert stage["status"]=="COMPLETE"
                assert (stage["begin_boundary"],stage["end_boundary"])==bound
                assert type(stage["begin_ns"]) is int and type(stage["end_ns"]) is int and 0<previous<=stage["begin_ns"]<=stage["end_ns"]<=full["end_ns"]
                previous=stage["end_ns"]
            row={"condition":condition,"run_id":run,"tp_rank":rank,"pid_in_container":full["pid"],"host_owner":owner,"device_uuid":cap["device_uuid"],"clock_identity":full["clock_identity"],"loader_type":full["loader_type"],"model_instance_id":full["model_instance_id"],"stages":stages,"source":refs[rel],"original_full_file":original}
            ranks.append(row);case_ranks.append(row)
            for name,stage in stages.items():
                elapsed=duration(stage["begin_ns"],stage["end_ns"]);assert elapsed is not None
                spans[name].append((stage["begin_ns"],stage["end_ns"],rank))
                rankcsv.append(dict(condition=condition,run_id=run,tp_rank=rank,stage=name,begin_ns=stage["begin_ns"],end_ns=stage["end_ns"],duration_s=elapsed,pid_in_container=full["pid"],host_pid=owner["pid"],host_startticks=owner["startticks"],boot_id=full["clock_identity"]["boot_id"],time_namespace=full["clock_identity"]["time_namespace"],monotonic_clock_implementation=full["clock_identity"]["monotonic_clock_implementation"],begin_boundary=stage["begin_boundary"],end_boundary=stage["end_boundary"],capsule_path=refs[rel]["export_path"],capsule_sha256=refs[rel]["export_sha256"],original_full_file_path=original["path"],original_full_file_sha256=original["sha256"]))
        envelopes={}
        for name,values in spans.items():
            left=min(x[0] for x in values);right=max(x[1] for x in values);largest=max(x[1]-x[0] for x in values)
            envelopes[name]={"begin_ns":left,"end_ns":right,"span_ns":right-left,"span_s":duration(left,right),"max_rank_duration_ns":largest,"max_rank_duration_s":largest/1e9,"begin_ranks":[x[2] for x in values if x[0]==left],"end_ranks":[x[2] for x in values if x[1]==right],"max_duration_ranks":[x[2] for x in values if x[1]-x[0]==largest],"per_rank_duration_s":[(x[1]-x[0])/1e9 for x in values]}
            known=aggregate if name=="full" else aggregate["stage_spans"][name]
            assert known["begin_ns"]==left and known["end_ns"]==right
            assert known["span_ns"]==right-left
            assert math.isclose(known["span_s"] if name!="full" else known["weight_load_host_span_s"],(right-left)/1e9,abs_tol=1e-9)
            assert (known["max_rank_weight_load_host_duration_ns"] if name=="full" else known["max_rank_duration_ns"])==largest
        if prepbegin and prepend:same_domain(prepbegin["clock_identity"],prepend["clock_identity"])
        prep=duration(prepbegin["monotonic_ns"],prepend["monotonic_ns"]) if prepbegin and prepend else None
        gap=duration(prepend["monotonic_ns"],times.get("registration_command_ns")) if prepend and arithmetic else None
        A=duration(times.get("registration_command_ns"),times.get("json_parse_complete_ns"))
        A_received=duration(times.get("registration_command_ns"),times.get("received_ns"))
        if arithmetic:assert arithmetic["primary_A_ns"]==(times["json_parse_complete_ns"]-times["registration_command_ns"])
        native=read(prefix+"controller-unit-span.json",True);controller=None
        if native and native.get("observation_status")=="OBSERVED":
            assert native["run_id"]==run and native["termination_class"]=="NORMAL_EXIT0" and native["clock_proof"]["status"]=="VALID"
            proof=native["clock_proof"];same_domain(proof["child_phase_clock_identity"],rootclock)
            assert proof["boot_id"]==rootclock["boot_id"] and proof["collector_time_namespace"]==proof["manager_time_namespace"]==rootclock["time_namespace"]
            assert native["start_ns"]==native["native_start_us"]*1000 and native["end_ns"]==native["native_exit_us"]*1000 and native["span_ns"]==native["end_ns"]-native["start_ns"]
            controller=duration(native["start_ns"],native["end_ns"])
        metrics=dict(full_span_s=envelopes["full"]["span_s"],ingestion_span_s=envelopes["weight_ingestion"]["span_s"],postprocessing_span_s=envelopes["postprocessing"]["span_s"],full_max_rank_s=envelopes["full"]["max_rank_duration_s"],ingestion_max_rank_s=envelopes["weight_ingestion"]["max_rank_duration_s"],postprocessing_max_rank_s=envelopes["postprocessing"]["max_rank_duration_s"],Prep_s=prep,Gap_s=gap,A_parse_s=A,A_received_s=A_received,B_arithmetic_received_s=duration(times.get("business_trigger_ns"),times.get("received_ns")),D_fixed32_received_s=duration(request["fixed32"]["producer_times"].get("business_trigger_ns"),request["fixed32"]["producer_times"].get("received_ns")) if "fixed32" in request else None,native_Controller_s=controller,controller_internal_elapsed_s=final.get("elapsed"))
        used=[prefix+n for n in ("controller-final.json","sample-validity.json","sample-metrics.json","cleanup-result.json","condition-terminal.json","unified-loading-host-span.json","load-evidence-original-files-before.json","load-evidence-original-files-after.json","ownership-capsule.json","event-target_condition_construction_begin.json","event-cache_pool_admission_complete.json","controller-unit-span.json") if prefix+n in refs]
        attempts.append({"condition":condition,"run_id":run,"block":1,"qualified":True,"envelopes":envelopes,"metrics":metrics,"producer_http_times":times,"native_controller":native,"sources":[refs[x] for x in used]})
        attemptcsv.append(dict(condition=condition,run_id=run,n=1,**metrics))
    assert len(ranks)==32 and len(rankcsv)==96 and len(attempts)==4
    summary={a["condition"]:{metric:{"raw":[value] if value is not None else [None],"n":int(value is not None),"mean":value,"median":value} for metric,value in a["metrics"].items()} for a in attempts}
    definitions={"full_span":"max over8 full.end_ns - min over8 full.begin_ns; host selected loader.load_model call, before optional runner setup; no added CUDA sync.","inner_spans":"Each stage independently max(end)-min(begin), plus separate max per-rank(end-begin). Cross-rank skew may overlap ingestion/postprocessing envelopes; do not add them or subtract to invent construction/IO.","SF_ingestion":"self.load_weights; includes any online quantization during weight loading, not pure IO.","SF_postprocessing":"before online-quant predicate through optional finalize and process_weights_after_loading return; branch execution not separately observed.","Store_ingestion":"load_dict through all restored data bindings and remaining buffer.to moves; host waits, not an independently synchronized pure CUDA interval.","Store_postprocessing":"Runtime-hook traversal through attention processing return.","Prep_s":"Controller target_condition_construction_begin to cache_pool_admission_complete; cache/pool admission prep only, excludes common environment and Store pool creation.","Gap_s":"Cache/pool admission complete to actual HTTP client's registration_command_ns, same validated clock domain.","A_parse_s":"Same HTTP producer registration_command_ns through arithmetic json_parse_complete_ns; excludes later received/context-close/validation/save.","A_received_s":"Same start through arithmetic received_ns; separately retained secondary interval.","native_Controller_s":"Actual systemd ExecMainStartTimestampMonotonic to ExecMainExitTimestampMonotonic, integer microsecond source precision; includes full child process lifecycle and finally/tail, excludes parent launch/external later release; never Prep+A.","D_fixed32_received_s":"Ready fixed32 HTTP producer business_trigger_ns to received_ns; wholeHTTP, not per-token latency."}
    result={"schema":"kimi-one-block-load-stages-derived-v1","cohort_id":BLOCK.removesuffix("-b01"),"complete_block_n":1,"qualified_attempt_n":4,"historical_original_primary_blocks_n":8,"historical_original_qualified_samples_n":38,"pooling_policy":"This supplementary block is separate; no pooling with original38/40 or8completeblocks, prior failed/diagnostic attempts.","condition_order":list(ORDER),"source_commit":block["source_commit"],"protocol_sha256":block["protocol_sha256"],"input_manifest_path":str(root/"EXPORT-MANIFEST.json"),"input_manifest_sha256":EXPECTED_MANIFEST,"verified_export_files":len(manifest),"verified_export_bytes":sum(x["bytes"] for x in manifest),"raw_original_rank_files_not_reread":"Read only exported small capsules and before/after whole-file hash receipts; original1MB rank files remain private and are not independently rehashed here.","definitions":definitions,"attempts":attempts,"ranks":ranks,"by_condition":summary,"poolhit_skew_note":"Postprocessing envelope19.362923020s vs max single-rank0.011842329s reflects inter-rank skew; not a19s per-rank postprocessing call.","parent_native_controller_span":None,"parent_native_controller_reason":"Parent unit GC; no actual retained native parent endpoints, no stitched estimate."}
    out.mkdir(parents=True,exist_ok=True)
    outputs=["endpoints-and-summary.json","rank-endpoints.csv","attempt-metrics.csv","condition-statistics.csv","summary.md","DERIVED-MANIFEST.json"]
    assert all(not (out/name).exists() for name in outputs),"refusing output overwrite"
    write_json(out/outputs[0],result);write_csv(out/outputs[1],rankcsv);write_csv(out/outputs[2],attemptcsv)
    statrows=[dict(condition=c,metric=k,raw=json.dumps(v["raw"]),n=v["n"],mean=v["mean"],median=v["median"]) for c,metrics in summary.items() for k,v in metrics.items()]
    write_csv(out/outputs[3],statrows)
    lines=["# Kimi supplemental load stages: one complete block","","Separate cohort; n=1 per condition. No pooling with original8 blocks/38 samples. Values below are raw=mean=median in seconds; no significance inference.","","| Condition | Full envelope | Ingestion envelope | Post envelope | Max-rank post | Prep | A parse | Native controller |","|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a in attempts:
        v=a["metrics"];keys=("full_span_s","ingestion_span_s","postprocessing_span_s","postprocessing_max_rank_s","Prep_s","A_parse_s","native_Controller_s")
        lines.append("| "+a["condition"]+" | "+" | ".join("NA" if v[k] is None else f"{v[k]:.9f}" for k in keys)+" |")
    lines+=["","Full and inner envelopes use max(end)-min(begin). Inner envelopes are not additive and are not pure disk IO/CUDA completion. Pool-hit post envelope19.362923020s versus max-rank0.011842329s shows rank skew; see all32rank endpoints.","","Controller is the retained native child process interval, not Prep+A. The parent native interval is missing after GC and remains null.","","Provenance and exact boundary definitions are in endpoints-and-summary.json. All284exported small files were size/SHA-verified; original full rank files were not reread. Missing optional observations remain null."]
    with (out/outputs[4]).open("x",encoding="utf-8") as f:f.write("\n".join(lines)+"\n")
    outputs_ref=[{"path":str(out/name),"sha256":sha((out/name).read_bytes()),"bytes":(out/name).stat().st_size} for name in outputs[:-1]]
    write_json(out/outputs[-1],{"input_manifest_path":str(root/"EXPORT-MANIFEST.json"),"input_manifest_sha256":EXPECTED_MANIFEST,"script_path":str(pathlib.Path(__file__).resolve()),"script_sha256":sha(pathlib.Path(__file__).read_bytes()),"files":outputs_ref,"CPU_only":True})
    print(json.dumps({"complete_block_n":1,"rank_rows":32,"rank_stage_csv_rows":96,"outputs":outputs_ref},ensure_ascii=False))
if __name__=="__main__":main()
