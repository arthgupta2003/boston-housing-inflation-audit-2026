#!/usr/bin/env python3
"""ACS PUMS audit of GBHRC 2026 p48. Python 3.10+, pandas, numpy, requests.
Run: python analyze_housing.py --data-dir data --output-dir results
Downloads Census household microdata, geographic correspondence, BLS CPI.
No IPUMS account or Census API key needed. Never uses person weights.
"""
from __future__ import annotations
import argparse, hashlib, io, json, re, zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import requests

URLS = {
 'acs2006_hma.zip': 'https://www2.census.gov/programs-surveys/acs/data/pums/2006/csv_hma.zip',
 'acs2024_hma.zip': 'https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/csv_hma.zip',
 'geo2000.html': 'https://usa.ipums.org/usa/volii/PUMA_composition_MA.shtml',
 'geo2020.txt': 'https://www2.census.gov/geo/docs/maps-data/data/rel2020/puma520/tab20_puma520_cousub20_natl.txt',
 'cpi2006.json': 'https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0?startyear=2006&endyear=2006&annualaverage=true',
 'cpi2024.json': 'https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0?startyear=2024&endyear=2024&annualaverage=true',
}
# Annual averages from BLS historical CPI-U December 2024, p5; kept explicitly
# because unauthenticated BLS API requests cannot span nineteen years.
CPI = {2006: 201.6, 2024: 313.689}
CPI_SOURCE = 'https://www.bls.gov/cpi/tables/supplemental-files/historical-cpi-u-202412.pdf'
COUNTIES = {'009','017','021','023','025'}


def download(data: Path):
    def one(item):
        name,url=item; p=data/name
        if not p.exists():
            r=requests.get(url,timeout=180);r.raise_for_status();p.write_bytes(r.content)
        return {'file':name,'url':url,'bytes':p.stat().st_size,
                'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    # BLS table is the authoritative index input; no API registration needed.
    items=URLS
    with ThreadPoolExecutor(max_workers=4) as pool: manifest=list(pool.map(one,items.items()))
    manifest.append({'url':CPI_SOURCE,'annual_CPI_U':CPI,'note':'Published annual averages, not December-to-December indices.'})
    return manifest


def geography(data: Path):
    rows=[]
    for table in re.findall(r'<table\b.*?</table>',(data/'geo2000.html').read_text(),re.S):
        # Negative lookbehind avoids accidentally matching "Super-PUMA".
        p=re.search(r'(?<!-)PUMA: (\d+)',table)
        if not p: continue
        counties=[]; pops=[]
        for tr in re.findall(r'<tr>.*?</tr>',table,re.S):
            td=[re.sub('<[^>]+>','',t).strip() for t in re.findall(r'<td[^>]*>(.*?)</td>',tr,re.S)]
            # County subtotal rows only: do not double count town/place/tract rows.
            if len(td)==8 and 'County' in td[0] and not any(td[4:7]):
                counties.append(td[3]);pops.append(int(td[1]))
        assert pops
        rows.append({'year':2006,'puma':int(p[1]),'counties':','.join(sorted(set(counties))),
                     'inside_fraction':sum(n for c,n in zip(counties,pops) if c in COUNTIES)/sum(pops),
                     'population_2000':sum(pops)})
    old=pd.DataFrame(rows)
    assert old.puma.nunique()==len(old)==52
    new=pd.read_csv(data/'geo2020.txt',sep='|',dtype=str)
    new=new[new.GEOID_PUMA5_20.str.startswith('25',na=False)].copy()
    new['county']=new.GEOID_COUSUB_20.str[2:5]
    new['inside']=new.county.isin(COUNTIES)
    rows=[]
    for p,s in new.groupby('GEOID_PUMA5_20'):
        assert s.inside.nunique()==1, f'2020 PUMA {p} crosses target boundary'
        rows.append({'year':2024,'puma':int(p[2:]),'counties':','.join(sorted(set(s.county))),
                     'inside_fraction':float(s.inside.iloc[0]),'population_2000':None})
    new=pd.DataFrame(rows)
    assert len(new)==54 and (new.inside_fraction==1).sum()==35
    return pd.concat([old,new],ignore_index=True)


def households(data: Path, year: int):
    with zipfile.ZipFile(data/f'acs{year}_hma.zip') as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv')]
        assert len(names)==1
        d=pd.read_csv(z.open(names[0]),low_memory=False)
    d.columns=d.columns.str.upper()
    assert d.SERIALNO.is_unique
    # TEN excludes vacancies and group quarters; NP/WGTP add explicit checks.
    r=d[d.TEN.isin([3,4]) & (d.NP>0) & (d.WGTP>0)].copy()
    r['INC_RAW']=r.HINCP
    r['INC_ADJUSTED']=r.HINCP*r['ADJUST' if year==2006 else 'ADJINC']/1e6
    # Interview-month housing cost: already a monthly amount. ADJHSG=1 in 2024.
    # IPUMS explains monthly rent needs no within-year adjustment, unlike income.
    r['RENT']=np.where(r.TEN==4,0,r.GRNTP)
    assert r.RENT.notna().all()
    return r


def select_geo(r, g, mode):
    g=g.set_index('puma');f=r.PUMA.map(g.inside_fraction)
    if mode=='majority': keep=f>.5; mult=1
    elif mode=='fractional': keep=f>0; mult=f
    elif mode=='inside': keep=f==1; mult=1
    elif mode=='touching': keep=f>0; mult=1
    elif mode=='essex_suffolk':
        keep=r.PUMA.map(g.counties).isin(['009','025']);mult=1
    else: raise ValueError(mode)
    out=r[keep].copy()
    out['GEO_MULT']=mult.loc[out.index] if isinstance(mult,pd.Series) else mult
    return out


def estimate(r, lo, hi, income='raw', burden='ratio', universe='all', threshold=.5):
    inc=r['INC_RAW' if income=='raw' else 'INC_ADJUSTED']
    keep=(inc>=lo)&(inc<hi)
    if universe=='valid': keep &= r.GRPIP.notna()
    elif universe=='positive': keep &= inc>0
    if burden=='grpip': outcome=r.GRPIP.gt(threshold*100)
    else:
        # Ratios are undefined for nonpositive income. Require explicit exclusion
        # rather than silently classifying zero or negative incomes.
        keep &= inc>0
        outcome=12*r.RENT>threshold*inc
    s=r.loc[keep];outcome=outcome.loc[keep].to_numpy(dtype=float)
    assert len(s)>0
    w=s.WGTP.to_numpy()*s.GEO_MULT.to_numpy();num=float(w@outcome);den=float(w.sum());p=num/den
    wr=s[[f'WGTP{i}' for i in range(1,81)]].to_numpy()*s.GEO_MULT.to_numpy()[:,None]
    pr=(wr*outcome[:,None]).sum(axis=0)/wr.sum(axis=0)
    # ACS successive-difference replicate variance. Geography/CPI are held fixed.
    se=float(np.sqrt(4/80*np.sum((pr-p)**2)))
    return dict(n=len(s),weighted_households=den,weighted_severe=num,percent=100*p,
                se_pp=100*se,ci90_low=max(0,100*(p-1.645*se)),ci90_high=min(100,100*(p+1.645*se)))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data-dir',type=Path,default=Path('data'));ap.add_argument('--output-dir',type=Path,default=Path('results'));args=ap.parse_args()
    args.data_dir.mkdir(parents=True,exist_ok=True);args.output_dir.mkdir(parents=True,exist_ok=True)
    manifest=download(args.data_dir);geo=geography(args.data_dir);geo.to_csv(args.output_dir/'geography.csv',index=False)
    for y in (2006,2024):
        j=json.loads((args.data_dir/f'cpi{y}.json').read_text())
        annual=[v for v in j['Results']['series'][0]['data'] if v['period']=='M13']
        assert len(annual)==1 and float(annual[0]['value'])==CPI[y]
    f=CPI[2024]/CPI[2006];hs={y:households(args.data_dir,y) for y in (2006,2024)}
    comparisons=[]
    for mode in ('majority','fractional','inside','touching','essex_suffolk'):
        for income in ('raw','adjusted'):
            for burden in ('ratio','grpip'):
                for basis in ('nominal','real_2006_anchor','real_2024_anchor'):
                    pair=[]
                    for y in (2006,2024):
                        scale=f if y==2024 and basis=='real_2006_anchor' else 1/f if y==2006 and basis=='real_2024_anchor' else 1
                        r=select_geo(hs[y],geo[geo.year==y],mode)
                        e=estimate(r,35000*scale,50000*scale,income,burden)
                        pair.append(e)
                        comparisons.append(dict(geography=mode,income=income,burden=burden,basis=basis,year=y,lower=35000*scale,upper_exclusive=50000*scale,**e))
                    # Difference uncertainty assumes independent annual samples.
                    comparisons[-1]['change_pp']=pair[1]['percent']-pair[0]['percent']
                    comparisons[-1]['change_se_pp']=float(np.hypot(pair[0]['se_pp'],pair[1]['se_pp']))
    table=pd.DataFrame(comparisons);table.to_csv(args.output_dir/'comparison.csv',index=False)
    broader=[]
    for y in (2006,2024):
        r=select_geo(hs[y],geo[geo.year==y],'majority')
        for basis in ('nominal','real_2006_anchor'):
            scale=f if y==2024 and basis=='real_2006_anchor' else 1
            for label,a,b in [('below_35k',0,35000),('35k_50k',35000,50000),('50k_75k',50000,75000),('75k_plus',75000,np.inf)]:
                e=estimate(r,a*scale,b*scale,'raw','ratio','positive');broader.append(dict(year=y,basis=basis,group=label,**e))
        for universe in ('all','valid','positive'):
            for threshold in (.3,.5):
                e=estimate(r,-np.inf,np.inf,'raw','grpip',universe,threshold);broader.append(dict(year=y,basis='all_incomes',group=f'{universe}_grpip_gt{int(threshold*100)}',**e))
    pd.DataFrame(broader).to_csv(args.output_dir/'broader.csv',index=False)
    main=table[(table.geography=='majority')&(table.income=='raw')&(table.burden=='ratio')]
    out={'cpi':CPI,'inflation_factor':f,'main_comparisons':main.astype(object).where(pd.notna(main),None).to_dict('records'),'download_manifest':manifest}
    (args.output_dir/'results.json').write_text(json.dumps(out,indent=2))
    print(main[['basis','year','lower','upper_exclusive','n','weighted_households','percent','ci90_low','ci90_high','change_pp']].to_string(index=False))
    print('\nBroader:',pd.DataFrame(broader).to_string(index=False))

if __name__=='__main__': main()
