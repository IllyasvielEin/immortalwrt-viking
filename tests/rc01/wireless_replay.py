"""Replay public backports maintenance patches; optional independent DFS patch."""
from pathlib import Path
import hashlib, subprocess, sys
root=Path(__file__).resolve().parents[2]; work=Path(sys.argv[2]).resolve(); work.mkdir(exist_ok=False)
archive=Path(sys.argv[1])
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='6ec76a4cb0988b5382b2fc5053610a56ada90b8ef6a5a4f2807cd433badb9454'
subprocess.run(['tar','--zstd','-xf',str(archive),'-C',str(work)],check=True)
source=work/'backports-7.2';count=0
with (work/'replay.log').open('w') as log:
 for group in ['build','subsys','ath','ath5k','ath9k','ath10k','ath11k','ath12k','rt2x00','mt7601u','mwl','brcm','rtl']:
  d=root/'package/kernel/mac80211/patches'/group; series=d/'series'
  patches=([d/x.strip() for x in series.read_text().splitlines() if x.strip() and not x.startswith('#')] if series.exists() else sorted(d.glob('*.patch')))
  for p in patches:
   if group=='ath12k' and p.name.startswith('924-01') and len(sys.argv)>3:
    dfs=Path(sys.argv[3])
    log.write(str(dfs)+'\n');log.flush()
    result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(source)],input=dfs.read_bytes(),stdout=log,stderr=subprocess.STDOUT)
    if result.returncode: raise SystemExit('failed DFS joint replay')
    count+=1
   if group=='ath11k' and p.name.startswith('nss-'): continue
   log.write(str(p.relative_to(root))+'\n');log.flush()
   result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(source)],input=p.read_bytes(),stdout=log,stderr=subprocess.STDOUT)
   if result.returncode: raise SystemExit(f'failed {p}')
   count+=1
print(f'{count} patches replayed fuzz=0: {source}')
