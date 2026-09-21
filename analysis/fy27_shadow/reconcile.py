"""Offline FY27 Excel shadow audit. Never saves the source or writes a database."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.headcount.calculation import CombinationInput, RateInput, calculate_headcounts
from packages.revenue.calculation import RevenueInvariantError, RevenueLineInput, calculate_revenue
from packages.class_size.validation import ClassSizeInput, HeadcountInput, validate_class_sizes

OUT = ROOT / 'output/fy27-shadow'
D = lambda value: Decimal(str(value))
SEASONS = ('暑假', '秋季', '寒假', '春季')
# renewal, share, old, new, total, price, discount, tuition, list revenue,
# business revenue, recognition, current, quarter, cross
COLS = [
    'AI AJ AL AM AN AO AP AQ AR AS AV AW AX AY'.split(),
    'AZ BA BC BD BE BF BG BH BI BJ BM BN BO BP'.split(),
    'BQ BR BT BU BV BW BX BY BZ CA CD CE CF CG'.split(),
    'CH CI CK CL CM CN CO CP CQ CR CU CV CW CX'.split(),
]


def half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def value(row, col):
    return row['cells'].get(col, {}).get('value')


def formula(row, col):
    return row['cells'].get(col, {}).get('formula')


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def write_csv(path, records):
    if not records:
        path.write_text('', encoding='utf-8')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def parameter_maps(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    maps, duplicates = {}, []
    for key_col, val_col in [('F', 'G'), ('M', 'N'), ('T', 'U'), ('AB', 'AC'), ('AM', 'AN'), ('AS', 'AT')]:
        index = {}
        for row in rows:
            if row['row'] < 3 or value(row, key_col) is None:
                continue
            key = value(row, key_col)
            if key in index:
                duplicates.append({'block': key_col, 'key': key, 'row': row['row']})
            else:  # Excel VLOOKUP exact returns the first occurrence.
                index[key] = (value(row, val_col), row['row'])
        maps[key_col] = index
    return maps, duplicates


def replay_new(old: int, share: Decimal, minimum: int, topup: bool) -> tuple[int, int]:
    if not 0 <= share < 1:
        raise ValueError('Inverse share outside [0, 1)')
    natural = half_up(D(old) / (1 - share) * share)
    return (max(natural, minimum - old) if topup else natural), natural


def audit(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    rows = [r for r in payload['sheets']['滚动表']['rows'] if r['row'] >= 2 and value(r, 'A') is not None]
    params, duplicates = parameter_maps(payload['sheets']['参数表']['rows'])
    checks, lookups, topups, normalized, unsupported = [], [], [], [], []
    errors = [{'sheet': s, 'cell': f'{c}{r["row"]}', 'error': item['value']}
              for s in ('滚动表', '参数表') for r in payload['sheets'][s]['rows']
              for c, item in r['cells'].items() if item['data_type'] == 'e']

    def check(row, col, actual, layer, tolerance=Decimal('0.00001')):
        expected = value(row, col)
        numeric = isinstance(expected, (float, int, Decimal)) and isinstance(actual, (float, int, Decimal))
        delta = D(actual) - D(expected) if numeric else None
        passed = abs(delta) <= tolerance if numeric else actual == expected
        checks.append({'row': row['row'], 'cell': f'{col}{row["row"]}', 'layer': layer,
                       'cached': expected, 'replayed': actual, 'delta': delta,
                       'tolerance': tolerance, 'passed': passed})

    def lookup(row, output, block, key, fallback=None, allow_missing=False):
        hit = params[block].get(key)
        # An exact match to an empty result cell yields numeric zero in Excel.
        result = (0 if hit[0] is None else hit[0]) if hit else fallback
        lookups.append({'cell': f'{output}{row["row"]}', 'block': block, 'key': key,
                        'parameter_row': hit[1] if hit else None, 'found': hit is not None,
                        'excel_fallback_allowed': allow_missing, 'matched_blank': bool(hit and hit[0] is None), 'value': result})
        if hit is None and not allow_missing:
            raise ValueError(f'Missing parameter for {output}{row["row"]}: {key}')
        check(row, output, result, 'parameter_lookup')
        return result

    def expect_formula(row, col, expected):
        if formula(row, col) != expected:
            raise ValueError(f'Unsupported formula at {col}{row["row"]}: {formula(row,col)}')

    for row in rows:
        n = row['row']
        track, product, grade, subject, campus = [value(row, c) for c in ('A', 'B', 'D', 'F', 'G')]
        prior_total, prior_cross, annual = 0, D(value(row, 'AG')), Decimal(0)
        for i, cols in enumerate(COLS):
            renewal_c, share_c, old_c, new_c, total_c, price_c, disc_c, tuition_c, list_c, biz_c, recog_c, current_c, quarter_c, cross_c = cols
            helper = ('P', 'Q', 'R', 'S')[i]
            renewal_lookup = f'VLOOKUP(${"U" if i == 0 else helper}:${"U" if i == 0 else helper},参数表!$AM:$AN,2,0)'
            expect_formula(row, renewal_c, f'=IFERROR({renewal_lookup},"-")' if i == 0 else '='+renewal_lookup)
            for col, keycol, first, last in [(share_c, helper, 'AB', 'AC'),
                    (price_c, ('H','I','J','K')[i], 'M','N'),
                    (disc_c, ('L','M','N','O')[i], 'T','U'),
                    (recog_c, ('H','I','J','K')[i], 'AS','AT')]:
                expect_formula(row, col, f'=VLOOKUP(${keycol}:${keycol},参数表!${first}:${last},2,0)')
            key = track + product + grade + SEASONS[i] + campus
            # Summer renewal uses the prior-grade field E, not the current grade D.
            renew_key = track + product + str(value(row, 'E')) + SEASONS[i] + campus if i == 0 else key
            renewal = lookup(row, renewal_c, 'AM', renew_key, '-', i == 0)
            share = D(lookup(row, share_c, 'AB', key))
            price = D(lookup(row, price_c, 'M', track + product + SEASONS[i]))
            discount = D(lookup(row, disc_c, 'T', track + product + grade + SEASONS[i]))
            recognition = D(lookup(row, recog_c, 'AS', track + product + SEASONS[i]))
            old_formula = formula(row, old_c)
            expected_old = f'=ROUND({"AE" if i == 0 else COLS[i-1][4]}{n}*{renewal_c}{n},0)'
            if old_formula is None:
                old = value(row, old_c)
                if i != 0 or old != 0:
                    unsupported.append({'cell': f'{old_c}{n}', 'formula': old_formula, 'value': old})
            elif old_formula == expected_old:
                old = half_up(D(value(row, 'AE') if i == 0 else prior_total) * D(renewal))
            else:
                raise ValueError(f'Unsupported old formula: {old_c}{n}: {old_formula}')
            new_formula = formula(row, new_c)
            complement = ('AK', 'BB', 'BS', 'CJ')[i]
            simple = f'ROUND({old_c}{n}/{complement}{n}*{share_c}{n},0)'
            conditional = f'=IF({simple}+{old_c}{n}<AH{n},AH{n}-{old_c}{n},{simple})'
            entry_formula = '=VLOOKUP($T:$T,参数表!$F:$G,2,0)'
            if i == 0 and new_formula in (entry_formula, f'=IFERROR({entry_formula[1:]},0)'):
                entry_key = track + product + grade + subject + campus
                new = lookup(row, new_c, 'F', entry_key, 0, 'IFERROR' in new_formula)
                natural = new
            elif new_formula in ('=' + simple, conditional):
                new, natural = replay_new(old, share, int(value(row, 'AH')), new_formula == conditional)
            else:
                raise ValueError(f'Unsupported new formula: {new_c}{n}: {new_formula}')
            if new > natural:
                topups.append({'row': n, 'cell': f'{new_c}{n}', 'season': SEASONS[i], 'track': track,
                               'product': product, 'grade': grade, 'subject': subject, 'campus': campus,
                               'old': old, 'natural_new': natural, 'excel_new': new, 'local_topup': new-natural})
            total = old + new
            expect_formula(row, total_c, f'={old_c}{n}+{new_c}{n}')
            expect_formula(row, ('AK','BB','BS','CJ')[i], f'=1-{share_c}{n}')
            for col, expected in [(tuition_c,f'={price_c}{n}*{disc_c}{n}'),
                (list_c,f'={total_c}{n}*{price_c}{n}'),(biz_c,f'={total_c}{n}*{tuition_c}{n}'),
                (current_c,f'={biz_c}{n}*{recog_c}{n}/1.06'),
                (cross_c,f'={biz_c}{n}*(1-{recog_c}{n})/1.06'),
                (quarter_c,f'={current_c}{n}+AG{n}' if i == 0 else
                 f'={current_c}{n}+{COLS[i-1][13]}{n}' if i == 3 else f'={COLS[i-1][13]}{n}+{current_c}{n}')]:
                expect_formula(row,col,expected)
            for col, result in [(old_c, old), (new_c, new), (total_c, total)]:
                check(row, col, result, 'headcount_formula', Decimal(0))
            tuition = price * discount
            list_revenue, business = D(total) * price, D(total) * tuition
            current = business * recognition / D('1.06')
            cross = business * (1-recognition) / D('1.06')
            quarter = current + prior_cross
            for col, result in [(tuition_c, tuition), (list_c, list_revenue), (biz_c, business),
                                (current_c, current), (quarter_c, quarter), (cross_c, cross)]:
                check(row, col, result, 'revenue_formula', Decimal('0.01'))
            normalized.append({'row': n, 'season': 20271+i, 'season_name': SEASONS[i], 'track': track,
                               'product': product, 'entry': value(row, 'C'), 'grade': grade, 'subject': subject,
                               'campus': campus, 'old': old, 'new': new, 'total': total,
                               'renewal': renewal, 'share': share, 'price': price, 'discount': discount,
                               'recognition': recognition, 'opening': D(value(row, 'AG')) if i == 0 else D(0),
                               'business': business, 'list_revenue': list_revenue, 'quarter': quarter})
            prior_total, prior_cross = total, cross
            annual += quarter
        check(row, 'W', annual, 'annual_formula', Decimal('0.01'))
        expect_formula(row, 'W', f'=AX{n}+BO{n}+CF{n}+CW{n}')

    negative_rows = [r['row'] for r in rows if D(value(r, 'AG')) < 0]
    lines, all_lines, excluded = [], [], []
    for item in normalized:
        if item['row'] in negative_rows:
            excluded.append({'row': item['row'], 'season': item['season'], 'reason': 'NEGATIVE_REVENUE_INPUT'})
        for field in ('discount','recognition'):
            if item[field]*1000000 != half_up(item[field]*1000000):
                raise ValueError(f'Non-exact financial ppm input: {item["row"]} {field}')
        line = RevenueLineInput(
            result_id=f'excel-{item["row"]}-{item["season"]}', combination_stable_key=f'excel-{item["row"]}',
            fiscal_season=item['season'], track=item['track'], product=item['product'],
            entry_attribute='ENTRY' if item['entry'] == '是' else 'NON_ENTRY', grade=item['grade'],
            subject=item['subject'], campus=item['campus'], integer_total_students=item['total'],
            list_price_yuan=item['price'], detail_discount_rate_ppm=half_up(item['discount']*1000000),
            current_season_recognition_rate_ppm=half_up(item['recognition']*1000000),
            opening_carryover_revenue_yuan=item['opening'])
        all_lines.append(line)
        if item['row'] not in negative_rows:
            lines.append(line)
    full_revenue_error = None
    try:
        calculate_revenue(fiscal_year=2027, budget_tax_conversion_factor=D('1.06'),
                          lines=all_lines, composite_targets=[])
    except RevenueInvariantError as error:
        full_revenue_error = error.code
    revenue = calculate_revenue(fiscal_year=2027, budget_tax_conversion_factor=D('1.06'),
                                lines=lines, composite_targets=[])
    row_index = {r['row']: r for r in rows}
    kernel_checks = []
    kernel_rollup = defaultdict(lambda: {'system_yuan': D(0), 'compared_excel_yuan': D(0), 'compared_rows': 0})
    for result in revenue.rows:
        n = int(result.source.combination_stable_key.split('-')[1])
        i = result.source.fiscal_season-20271
        group = kernel_rollup[(result.source.campus, result.source.track, result.source.fiscal_season)]
        group['system_yuan'] += result.quarter_recognized_revenue_yuan
        group['compared_excel_yuan'] += D(value(row_index[n], COLS[i][12]))
        group['compared_rows'] += 1
        for col_index, attr in [(7, 'discounted_tuition_yuan'), (8, 'list_price_revenue_yuan'),
                                 (9, 'business_quarter_revenue_yuan'), (11, 'current_recognized_revenue_yuan'),
                                 (12, 'quarter_recognized_revenue_yuan'), (13, 'cross_season_revenue_yuan')]:
            col = COLS[i][col_index]
            actual = getattr(result, attr)
            delta = actual-D(value(row_index[n], col))
            kernel_checks.append({'cell': f'{col}{n}', 'season': result.source.fiscal_season,
                                  'cached': value(row_index[n], col), 'system': actual, 'delta': delta,
                                  'passed': abs(delta) <= D('0.01')})

    combinations, rates, rate_keys, precision_changes = [], [], {}, []
    for row in rows:
        combinations.append(CombinationInput(f'excel-{row["row"]}', value(row, 'A'), value(row, 'B'),
            'ENTRY' if value(row, 'C') == '是' else 'NON_ENTRY', value(row, 'D'), value(row, 'F'),
            value(row, 'G'), int(value(row, 'AM')) if value(row, 'C') == '是' else 0))
    for item in normalized:
        for typ, val in [('RENEWAL_RATE', item['renewal']), ('NEW_STUDENT_SHARE', item['share'])]:
            # The strict engine never consumes summer renewal or summer shares for positive entry counts.
            if item['season'] == 20271 and (typ == 'RENEWAL_RATE' or (item['entry'] == '是' and item['new'] > 0)):
                continue
            if not isinstance(val, (Decimal, int, float)):
                continue
            parameter_key = item['track']+item['product']+item['grade']+item['season_name']+item['campus']
            parameter = params['AM' if typ == 'RENEWAL_RATE' else 'AB'].get(parameter_key)
            if parameter is None or parameter[0] is None:
                continue  # Excel's implicit blank-to-zero is not an approved system parameter.
            ppm = half_up(D(val)*1000000)
            if typ == 'NEW_STUDENT_SHARE' and ppm > 999900:
                precision_changes.append({'row': item['row'], 'season': item['season'], 'type': typ,
                                           'source': val, 'mapped_ppm': None, 'reason': 'REVERSE_SHARE_OUT_OF_RANGE'})
                continue
            key = (typ, item['season'], item['track'], item['product'], item['grade'], item['campus'])
            if key in rate_keys and rate_keys[key] != ppm:
                raise ValueError(f'Conflicting source rates: {key}')
            if key not in rate_keys:
                rate_keys[key] = ppm
                rates.append(RateInput(f'rate-{len(rates)+1}', *key, ppm))
                if D(ppm) != D(val)*1000000:
                    precision_changes.append({'row': item['row'], 'season': item['season'], 'type': typ,
                                               'source': val, 'mapped_ppm': ppm, 'reason': 'PPM_QUANTIZATION'})
    headcount = calculate_headcounts(fiscal_year=2027, combinations=combinations, rates=rates, plans=[])
    minimums = {}
    for row in rows:
        key = value(row,'A'),value(row,'B')
        minimum = int(value(row,'AH'))
        if key in minimums and minimums[key] != minimum:
            raise ValueError(f'Conflicting class size minimum: {key}')
        minimums[key] = minimum
    class_sizes = validate_class_sizes(
        headcounts=[HeadcountInput(f'excel-{x["row"]}-{x["season"]}',f'excel-{x["row"]}',
            x['season'],x['track'],x['product'],'ENTRY' if x['entry']=='是' else 'NON_ENTRY',
            x['grade'],x['subject'],x['campus'],'CALCULATED',x['total']) for x in normalized],
        parameters=[ClassSizeInput(f'min-{i}',*key,val) for i,(key,val) in enumerate(minimums.items())])
    annual = []
    for track, target in [('部门合计', 113000000), ('智慧伴学', 37000000), ('智慧空间', 76000000)]:
        chosen = [r for r in rows if track == '部门合计' or value(r, 'A') == track]
        total = sum((D(value(r, 'W')) for r in chosen), D(0))
        annual.append({'scope': track, 'excel_yuan': total, 'target_yuan': target, 'difference_yuan': total-target})
    target_rates = {'部门合计': ['.86','.71','.89','.95','.95'], '智慧伴学':['.89','.82','.90','.94','.94'],
                    '智慧空间':['.84','.67','.88','.95','.95']}
    discounts = []
    for track, targets in target_rates.items():
        for i, label in enumerate(('全年',)+SEASONS):
            selected = [r for r in normalized if (track == '部门合计' or r['track'] == track)
                        and (i == 0 or r['season'] == 20270+i)]
            denominator = sum((r['list_revenue'] for r in selected), D(0))
            numerator = sum((r['business'] for r in selected), D(0))
            actual = numerator/denominator if denominator else None
            delta_pp = (actual-D(targets[i]))*100 if actual is not None else None
            discounts.append({'scope': track, 'season': label, 'numerator_yuan': numerator,
                              'denominator_yuan': denominator, 'actual_percent': actual*100 if actual else None,
                              'target_percent': D(targets[i])*100, 'difference_percentage_points': delta_pp,
                              'within_0_05pp': delta_pp is not None and abs(delta_pp) <= D('.05')})
    dimensions = [tuple(value(r,c) for c in ('A','B','D','F','G')) for r in rows]
    pivot = {r['row']: r for r in payload['sheets']['透视表']['rows']}
    summary = {
        'status': 'PARTIAL_SHADOW_BLOCKED_FOR_FULL_IMPORT', 'generated_at': datetime.now(UTC).isoformat(),
        'source_sha256': payload['sha256'], 'source_file': payload['source_file'],
        'source_extracted_at': payload['extracted_at'], 'business_snapshot_date': None, 'source_batch': None,
        'target_source': 'User-provided FY27 school budget notice in conversation; amounts in yuan',
        'scope_rows': len(rows), 'season_rows': len(normalized), 'unique_dimensions': len(set(dimensions)),
        'duplicate_dimensions': [list(k) for k,v in Counter(dimensions).items() if v > 1],
        'missing_dimensions': [r['row'] for r in rows if any(not value(r,c) for c in ('A','B','C','D','F','G'))],
        'parameter_duplicates': duplicates, 'primary_sheet_cached_errors': errors, 'unsupported_inputs': unsupported,
        'formula_checks': len(checks), 'formula_failures': sum(not c['passed'] for c in checks),
        'lookup_checks': len(lookups), 'missing_lookup_fallbacks': [x for x in lookups if not x['found']],
        'negative_opening_rows': negative_rows, 'negative_opening_total': sum((D(value(row_index[n],'AG')) for n in negative_rows),D(0)),
        'opening_old_rows': sum(value(r,'AL') > 0 for r in rows), 'opening_old_students': sum(value(r,'AL') for r in rows),
        'topup_row_seasons': len(topups), 'local_topup_sum_not_causal_annual_impact': sum(x['local_topup'] for x in topups),
        'all_year_zero_rows': sum(all(value(r,c[4]) == 0 for c in COLS) for r in rows),
        'entry_parameter_total': sum(D(x[0]) for x in params['F'].values() if x[0] is not None),
        'entry_new_total': sum(value(r,'AM') for r in rows if value(r,'C') == '是'),
        'seventh_grade_entry_new': sum(value(r,'AA') or 0 for r in rows),
        'pivot_annual_yuan': value(pivot[8],'I'), 'annual_targets': annual, 'discount_targets': discounts,
        'kernel_revenue_rows': len(revenue.rows), 'kernel_revenue_exclusions': excluded,
        'full_revenue_kernel_error': full_revenue_error,
        'kernel_revenue_checks': len(kernel_checks), 'kernel_revenue_failures': sum(not x['passed'] for x in kernel_checks),
        'kernel_revenue_max_abs_delta_yuan': max(abs(x['delta']) for x in kernel_checks),
        'kernel_revenue_validation_codes': dict(Counter(x.code for x in revenue.validations)),
        'same_input_class_size_validation_codes': dict(Counter(x.code for x in class_sizes.validations)),
        'strict_headcount_publishable': headcount.publishable,
        'strict_headcount_complete_rows': sum(x.integer_total_students is not None for x in headcount.rows),
        'strict_headcount_incomplete_rows': sum(x.integer_total_students is None for x in headcount.rows),
        'strict_headcount_validation_codes': dict(Counter(x.code for x in headcount.validations)),
        'rate_precision_or_range_changes': precision_changes,
        'matched_blank_parameter_cells': [x for x in lookups if x['matched_blank']],
        'money_comparison_tolerance_yuan': '.01', 'counts_comparison_tolerance': 0,
        'warning': 'Same-input revenue kernel comparison is not full headcount-to-revenue parity or publishing approval.'}
    grouped = defaultdict(lambda: {'old':0, 'new':0, 'total':0, 'quarter_yuan':D(0)})
    for item in normalized:
        group=grouped[(item['campus'], item['track'],item['season'])]
        for field in ('old','new','total'):
            group[field] += item[field]
        group['quarter_yuan'] += item['quarter']
    rollup = [{'campus':k[0],'track':k[1],'season':k[2],**v} for k,v in sorted(grouped.items())]
    revenue_rollup = []
    for key, totals in sorted(grouped.items()):
        compared = kernel_rollup[key]
        total_rows = sum((x['campus'],x['track'],x['season']) == key for x in normalized)
        delta = compared['system_yuan'] - compared['compared_excel_yuan']
        revenue_rollup.append({'campus':key[0], 'track':key[1], 'season':key[2],
            'all_excel_yuan':totals['quarter_yuan'], **compared, 'total_rows':total_rows,
            'fully_compared':compared['compared_rows']==total_rows, 'delta_on_compared_rows':delta,
            'passed_on_compared_rows':abs(delta) <= D('.01')})
    summary['revenue_rollup_groups'] = len(revenue_rollup)
    summary['revenue_rollup_partial_groups'] = sum(not x['fully_compared'] for x in revenue_rollup)
    summary['revenue_rollup_failures'] = sum(not x['passed_on_compared_rows'] for x in revenue_rollup)
    summary['code_sha256'] = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [Path(__file__), ROOT/'packages/headcount/calculation.py',ROOT/'packages/revenue/calculation.py',
                     ROOT/'packages/class_size/validation.py']}
    return summary, {'formula-checks': checks, 'parameter-lookups': lookups, 'excel-topups': topups,
                     'campus-track-season': rollup,
                     'campus-track-season-revenue-kernel': revenue_rollup,
                     'normalized-lines': normalized, 'revenue-kernel-checks': kernel_checks,
                     'headcount-validations': [asdict(x) for x in headcount.validations],
                     'class-size-validations': [asdict(x) for x in class_sizes.validations],
                     'strict-headcount-results': [{'row': x.combination.stable_id, 'season': x.fiscal_season,
                         'old': x.integer_old_students, 'new': x.integer_new_students, 'total': x.integer_total_students}
                                                 for x in headcount.rows]}


def main():
    payload = json.loads((OUT/'source-cells.json').read_text())
    source = ROOT/payload['source_file']
    if hashlib.sha256(source.read_bytes()).hexdigest() != payload['sha256']:
        raise ValueError('Workbook changed: re-extract before auditing')
    summary, evidence = audit(payload)
    for name, records in evidence.items():
        write_csv(OUT/f'{name}.csv', records)
    write_json(OUT/'summary.json', summary)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == payload['sha256']
    print(json.dumps({k: v for k,v in summary.items() if k not in ('missing_lookup_fallbacks','rate_precision_or_range_changes','matched_blank_parameter_cells')}, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
