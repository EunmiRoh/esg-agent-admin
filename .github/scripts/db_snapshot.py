"""월간 DB 스냅샷 — Supabase REST(anon, 읽기 전용)에서 집계값만 뽑아 reports/db-snapshot/에 저장한다.

저장소가 공개이므로 기업명·전문가 이름/이메일·자유 의견·회의록 원문 등 개인정보는 저장하지 않는다.
expert_name은 테스트 응답 제외(api/learn.js와 동일 기준)에만 쓰고 결과에는 남기지 않는다.

사용: python3 db_snapshot.py <index.html 경로> <출력 디렉터리>
"""
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
TEST_NAMES = {'노은미모바일', '노은미'}
DIMS = ['score_specificity', 'score_feasibility', 'score_industry_fit', 'score_structure', 'score_traceability']

SELECT = {
    'diagnosis_results': 'created_at,grade,company_industry,score,e_score,s_score,g_score,user_type',
    'survey_invitations': 'created_at,status,respondent_type,completed_at',
    'survey_responses': 'submitted_at,expert_name,respondent_type,' + ','.join(DIMS)
                        + ',overall_satisfaction,adoption_intent,nps_recommend',
    'meeting_feedback': 'created_at,n_points,method,status',
    'stage5_learning': 'created_at,n_experts,priority_dimension,priority_mean,priority_sd,status',
}


def read_config(index_html):
    src = open(index_html, encoding='utf-8').read()
    url = re.search(r"https://[a-z0-9]+\.supabase\.co", src)
    key = re.search(r"createClient\([^)]*?(eyJ[A-Za-z0-9_.-]+)", src, re.S)
    if not url or not key:
        sys.exit('index.html에서 Supabase 설정을 찾지 못했습니다.')
    return url.group(0), key.group(1)


def fetch(url, key, table):
    req = urllib.request.Request(
        f"{url}/rest/v1/{table}?select={SELECT[table]}",
        headers={'apikey': key, 'Authorization': f'Bearer {key}'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 2) if xs else None


def in_month(ts, start, end):
    if not ts:
        return False
    t = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return start <= t < end


def build(data, now):
    # 집계 기간: 직전 한 달(KST 기준 지난달 1일 ~ 이번 달 1일)
    this_month = now.astimezone(KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    period = (last_month, this_month)

    diag = data['diagnosis_results']
    inv = data['survey_invitations']
    resp = [r for r in data['survey_responses'] if (r.get('expert_name') or '').strip() not in TEST_NAMES]
    experts = [r for r in resp if r.get('respondent_type') != 'demand']
    demand = [r for r in resp if r.get('respondent_type') == 'demand']
    mtg = data['meeting_feedback']
    learn = sorted(data['stage5_learning'], key=lambda l: l['created_at'], reverse=True)

    return {
        'generated_at': now.astimezone(KST).isoformat(timespec='seconds'),
        'period': {'from': last_month.date().isoformat(), 'to': this_month.date().isoformat()},
        'diagnosis': {
            'total': len(diag),
            'last_month': sum(in_month(d['created_at'], *period) for d in diag),
            'grade_distribution': dict(Counter(d.get('grade') or '미상' for d in diag)),
            'industry_distribution': dict(Counter(d.get('company_industry') or '미상' for d in diag)),
            'mean_score': mean([d.get('score') for d in diag]),
            'mean_esg': {k: mean([d.get(k + '_score') for d in diag]) for k in 'esg'},
        },
        'survey': {
            'invitations_total': len(inv),
            'invitations_last_month': sum(in_month(i['created_at'], *period) for i in inv),
            'invitation_status': dict(Counter(i.get('status') or '미상' for i in inv)),
            'responses_total_excl_test': len(resp),
            'responses_last_month': sum(in_month(r['submitted_at'], *period) for r in resp),
            'expert': {'n': len(experts), 'dimension_means': {d: mean([r.get(d) for r in experts]) for d in DIMS}},
            'demand': {
                'n': len(demand),
                'overall_satisfaction': mean([r.get('overall_satisfaction') for r in demand]),
                'adoption_intent': mean([r.get('adoption_intent') for r in demand]),
                'nps_recommend': mean([r.get('nps_recommend') for r in demand]),
            },
        },
        'meeting_feedback': {
            'total': len(mtg),
            'last_month': sum(in_month(m['created_at'], *period) for m in mtg),
            'points_total': sum(m.get('n_points') or 0 for m in mtg),
            'status': dict(Counter(m.get('status') or '미상' for m in mtg)),
        },
        'stage5_learning': {
            'total': len(learn),
            'last_month': sum(in_month(l['created_at'], *period) for l in learn),
            'status': dict(Counter(l.get('status') or '미상' for l in learn)),
            'latest_at': learn[0]['created_at'] if learn else None,
            'history': [{k: l.get(k) for k in ('created_at', 'status', 'priority_dimension',
                                               'priority_mean', 'priority_sd', 'n_experts')} for l in learn],
        },
    }


def main():
    index_html, out_dir = sys.argv[1], sys.argv[2]
    url, key = read_config(index_html)
    data = {t: fetch(url, key, t) for t in SELECT}
    snap = build(data, datetime.now(timezone.utc))
    month = snap['period']['from'][:7]
    body = json.dumps(snap, ensure_ascii=False, indent=2) + '\n'
    for name in (f'{month}.json', 'latest.json'):
        with open(f'{out_dir}/{name}', 'w', encoding='utf-8') as f:
            f.write(body)
    print(f'스냅샷 저장: {month} (진단 {snap["diagnosis"]["total"]}, 응답 {snap["survey"]["responses_total_excl_test"]}, '
          f'디렉티브 {snap["stage5_learning"]["total"]})')


if __name__ == '__main__':
    main()
