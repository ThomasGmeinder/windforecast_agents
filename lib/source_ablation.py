#!/usr/bin/env python3
"""Read-only source-ablation experiment for hourly forecast components.

Never writes production state.  Candidate corrections exist only in memory.
"""
import argparse, collections, copy, json, os, random, sys
sys.path.insert(0, os.path.dirname(__file__))
import forecast as fc
import postproc, verify

LAKES = ("ammersee", "kochelsee", "walchensee")
MIN_DAYS, MIN_PAIRS = 10, 60

def rows(lake):
    out = []
    for vt, _issued, r in verify.hourly_forecast_of_record(lake):
        if r.get("measured_kn") is None or not r.get("blend_kn") or r.get("legacy_calendar_backfill"):
            continue
        vals = {k:v for k,v in r["blend_kn"].items() if isinstance(v, (int,float))}
        if vals: out.append((vt, r, vals))
    return out

def candidates(rs):
    keys = sorted({k for _,_,v in rs for k in v})
    return {"current equal blend": lambda v: list(v),
            **{f"without {k}": lambda v,k=k:[x for x in v if x != k] for k in keys},
            **{f"{k} only": lambda v,k=k:[k] if k in v else [] for k in keys}}

def evaluate(rs, chooser):
    state, out = {}, []
    for vt, r, values in rs:
        used = chooser(values)
        if not used: continue
        raw = sum(values[k] for k in used)/len(used)
        hour = int(vt[11:13]); key = fc._bucket_key(r.get("regime","gradient"), hour)
        st = state.setdefault(key, postproc.new_state())
        # Same guarded local mean correction, but a fresh state for this candidate.
        full = postproc.apply(st, raw, fc.BIAS_CAP_KN)
        pred = raw + min(1.0, st["n"]/fc.N_MIN_OBS)*(full-raw)
        err = pred-r["measured_kn"]
        out.append({"date":vt[:10],"err":err,"raw_err":raw-r["measured_kn"],"used":used})
        postproc.update(st, raw, r["measured_kn"])
    return out

def summary(xs):
    if not xs:return {"n":0,"days":0,"mae":None,"bias":None}
    return {"n":len(xs),"days":len({x["date"] for x in xs}),
            "mae":sum(abs(x["err"]) for x in xs)/len(xs),"bias":sum(x["err"] for x in xs)/len(xs)}

def report(lake):
    rs=rows(lake); cs=candidates(rs); got={name:evaluate(rs, fn) for name,fn in cs.items()}
    base=got["current equal blend"]; base_by={(x["date"],i):x for i,x in enumerate(base)}
    result={"lake":lake,"data":summary(base),"exploratory":True,"variants":[]}
    for name,xs in got.items():
        s=summary(xs); paired=[x["err"]-base[i]["err"] for i,x in enumerate(xs) if i<len(base)]
        s.update({"name":name,"delta_mae_vs_current":None if not paired else sum(abs(x["err"]) for x in xs)/len(xs)-summary(base)["mae"],
                  "sources_used":sorted({k for x in xs for k in x["used"]})})
        result["variants"].append(s)
    return result

def markdown(r):
    d=r["data"]; lines=[f"# {r['lake'].title()} — source ablation (read-only)","",
      f"**EXPLORATORY ONLY** — {d['n']} scored hours across {d['days']} day(s); decisions require {MIN_PAIRS} hours / {MIN_DAYS} days.","",
      "| Candidate | Hours | Days | MAE kn | Bias kn | Δ MAE vs current | Sources |","|---|---:|---:|---:|---:|---:|---|"]
    for x in r["variants"]:
      f=lambda v:"—" if v is None else f"{v:+.2f}" if x is not r["variants"][0] and v==x.get("delta_mae_vs_current") else f"{v:.2f}"
      lines.append(f"| {x['name']} | {x['n']} | {x['days']} | {f(x['mae'])} | {f(x['bias'])} | {f(x['delta_mae_vs_current'])} | {', '.join(x['sources_used'])} |")
    return "\n".join(lines)

def selftest():
    before={p:open(p,'rb').read() for p in (fc.bias_path('walchensee'),) if os.path.exists(p)}
    synthetic=[("2026-01-01T10:00:00+01:00",{"regime":"thermal","measured_kn":10}, {"good":10,"bad":2}) for _ in range(8)]
    got=evaluate(synthetic, lambda v:["good"]); assert summary(got)["mae"] < 0.1
    after={p:open(p,'rb').read() for p in before}; assert before==after
    print("source_ablation self-test: PASS read-only candidate state")

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument("lake",nargs="?",default="walchensee",choices=LAKES); ap.add_argument("--out"); ap.add_argument("--selftest",action="store_true"); a=ap.parse_args()
    if a.selftest:selftest(); raise SystemExit
    text=markdown(report(a.lake)); print(text)
    if a.out:
      os.makedirs(os.path.dirname(os.path.abspath(a.out)),exist_ok=True); open(a.out,'w').write(text+'\n')
