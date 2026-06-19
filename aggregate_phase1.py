#!/usr/bin/env python3
import os, re, csv, json, glob, statistics
from collections import defaultdict

RESULTS_DIR = 'results/dash'
OUT_DIR     = 'results/phase1_analysis'
MOB_DIR     = os.path.join(OUT_DIR, 'mobility')
SIG_RE = re.compile(
    r'car1 \((\d+),(\d+)\) t=(\d+) \| RSU=(\w+) \| sig=(-?\d+)dBm \| v=([\d.]+)km/h')

def parse_topo_log(path, rid):
    rows = []
    with open(path) as f:
        for line in f:
            m = SIG_RE.search(line)
            if m:
                x,y,t,rsu,sig,v = m.groups()
                rows.append((int(t),int(x),int(y),rsu,int(sig),float(v)))
    with open(os.path.join(MOB_DIR, rid+'_mobility.csv'),'w',newline='') as f:
        w=csv.writer(f); w.writerow(['t','x','y','rsu','rssi_dbm','speed_kmh']); w.writerows(rows)
    return [r[4] for r in rows]

def parse_handover_csv(path):
    vals=[]
    with open(path) as f:
        for row in csv.DictReader(f):
            if row.get('dpid')=='1':
                try: vals.append(float(row['handover_exec_ms']))
                except (ValueError,KeyError,TypeError): pass
    return vals

def mean_(x): return statistics.mean(x) if x else ''
def std_(x):  return statistics.pstdev(x) if len(x)>1 else 0.0

def main():
    os.makedirs(MOB_DIR, exist_ok=True)
    run_dirs = sorted(glob.glob(os.path.join(RESULTS_DIR,'sit*','speed*','dash_sit*_spd*_r*')))
    raw=[]
    for d in run_dirs:
        rid=os.path.basename(d)
        m=re.match(r'dash_sit(\d+)_spd(\d+)_r(\d+)$', rid)
        if not m: continue
        sit,speed,rnd=int(m.group(1)),int(m.group(2)),int(m.group(3))
        summ=os.path.join(d,rid+'_summary.json')
        topo=os.path.join(d,'topo_'+rid+'.log')
        ho  =os.path.join(d,rid+'_handover.csv')
        if not os.path.exists(summ): print('  [skip] no summary:',rid); continue
        s=json.load(open(summ))
        rssi=parse_topo_log(topo,rid) if os.path.exists(topo) else []
        hov =parse_handover_csv(ho) if os.path.exists(ho) else []
        raw.append({'arch':'dash','sit':sit,'speed':speed,'round':rnd,
            'avg_bitrate_kbps':s.get('avg_bitrate_kbps',''),
            'avg_throughput_kbps':s.get('avg_throughput_kbps',''),
            'startup_delay_s':s.get('startup_delay_s',''),
            'total_stall_events':s.get('total_stall_events',''),
            'total_stall_duration_s':s.get('total_stall_duration_s',''),
            'rebuffering_ratio':s.get('rebuffering_ratio',''),
            'quality_switches':s.get('quality_switches',''),
            'rssi_mean_dbm':round(statistics.mean(rssi),2) if rssi else '',
            'rssi_min_dbm':min(rssi) if rssi else '',
            'rssi_max_dbm':max(rssi) if rssi else '',
            'n_handovers':len(hov),
            'handover_ms_mean':round(statistics.mean(hov),4) if hov else '',
            'handover_ms_max':round(max(hov),4) if hov else ''})
    if not raw: print('No runs found under',RESULTS_DIR); return
    cols=list(raw[0].keys())
    raw_path=os.path.join(OUT_DIR,'dash_phase1_raw_60runs.csv')
    with open(raw_path,'w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(raw)
    print('Wrote %s  (%d runs)'%(raw_path,len(raw)))
    metrics=['avg_bitrate_kbps','avg_throughput_kbps','startup_delay_s',
             'total_stall_events','rebuffering_ratio','quality_switches',
             'rssi_mean_dbm','n_handovers','handover_ms_mean']
    grp=defaultdict(lambda:defaultdict(list))
    for r in raw:
        for mt in metrics:
            v=r[mt]
            if v!='' and v is not None: grp[(r['sit'],r['speed'])][mt].append(float(v))
    stats_path=os.path.join(OUT_DIR,'dash_phase1_stats_table.csv')
    with open(stats_path,'w',newline='') as f:
        w=csv.writer(f); header=['sit','speed','n']
        for mt in metrics: header+=[mt+'_mean',mt+'_sd']
        w.writerow(header)
        for key in sorted(grp):
            g=grp[key]; n=max((len(g[mt]) for mt in metrics),default=0); row=[key[0],key[1],n]
            for mt in metrics:
                v=g[mt]; row+=[round(mean_(v),4) if v else '', round(std_(v),4) if v else '']
            w.writerow(row)
    print('Wrote %s'%stats_path)
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        speeds=sorted(set(r['speed'] for r in raw)); sits=sorted(set(r['sit'] for r in raw))
        def series(sit,mt):
            out=[]
            for sp in speeds:
                v=[float(r[mt]) for r in raw if r['sit']==sit and r['speed']==sp and r[mt] not in ('',None)]
                out.append(statistics.mean(v) if v else float('nan'))
            return out
        panels=[('avg_bitrate_kbps','Avg bitrate (kbps)'),('rebuffering_ratio','Rebuffering ratio'),
                ('rssi_mean_dbm','Mean RSSI (dBm)'),('handover_ms_mean','Handover update time (ms)')]
        fig,axes=plt.subplots(2,2,figsize=(11,8))
        for ax,(mt,title) in zip(axes.flat,panels):
            for sit in sits: ax.plot(speeds,series(sit,mt),marker='o',label='Sit %d'%sit)
            ax.set_title(title); ax.set_xlabel('Speed (km/h)'); ax.set_xticks(speeds)
            ax.grid(True,alpha=0.3); ax.legend()
        fig.suptitle('SDN-DASH Phase 1 Overview'); fig.tight_layout()
        png=os.path.join(OUT_DIR,'dash_phase1_overview.png'); fig.savefig(png,dpi=130)
        print('Wrote %s'%png)
    except Exception as e:
        print('Plot skipped (%s) - CSVs still complete'%e)

if __name__=='__main__': main()
