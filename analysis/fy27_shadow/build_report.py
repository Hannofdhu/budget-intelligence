"""Create canonical local audit report plus a bounded runnable notebook companion."""
import contextlib
import io
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from reconcile import OUT, ROOT, write_json


def main():
    summary = json.loads((OUT/'summary.json').read_text())
    if summary['source_sha256'] != 'a94d1c541ca4a394d2181942c7b8e69e9bfce7dd41b6a1468a7ac9344717e336':
        raise ValueError('This narrative is scoped to the reviewed FY27 snapshot; review it before changing the source.')
    number = lambda x, places=2: f'{Decimal(str(x)):,.{places}f}'
    source = {'id':'workbook', 'label':'FY27 学校版预算原 Excel 保存值与公式',
              'path':summary['source_file'], 'query':{
                  'engine':'Python / OOXML read-only', 'language':'python',
                  'description':'滚动表第 2–239 行与参数表精确查找；原文件只读；学校版、FY27 四季度。',
                  'tables_used':['滚动表!A1:CX239','参数表!A1:AT274','透视表!I6:I8'],
                  'executed_at':summary['source_extracted_at']}}
    derived = {'id':'shadow','label':'离线核对脚本与冻结提取结果','path':'analysis/fy27_shadow/reconcile.py',
               'query':{'engine':'Python Decimal + production pure kernels','language':'python',
                        'description':'不入库、不优化参数；按源公式白名单复算，收入内核只核对可接受输入，缺失与异常保留。收入与折扣目标引用用户对话中的首轮学校版预算通知。',
                        'tables_used':['output/fy27-shadow/source-cells.json','output/fy27-shadow/summary.json'],
                        'executed_at':summary['generated_at']}}
    targets = {'id':'targets','label':'用户提供的 FY27 首轮学校版预算通知（对话图片）',
               'query':{'description':'部门及赛道年度收入目标、年度及四季折扣率目标。不是集团版；本轮未获更晚的目标版本。'}}
    chart_query=(ROOT/'analysis/fy27_shadow/department_discount.sql').read_text()
    chart_source={'id':'discount-query','label':'原 Excel 保存值的部门综合折扣汇总',
        'path':'analysis/fy27_shadow/department_discount.sql','query':{'engine':'SQLite JSON1 in-memory',
            'language':'sql','sql':chart_query,'description':
            '绑定 :source_json 为 source-cells.json 原始提取；直接汇总滚动表各期折后/原价收入，再对照用户通知目标。'
            '脚本另以 Decimal 全量复算交叉验证该图结果；二进制浮点仅用于画图，不用于阈值判定。',
            'tables_used':['rolling_cells','seasons','periods','targets'],
            'executed_at':summary['generated_at']}}
    blocks=[]
    def section(id, body, source_id=None):
        block={'id':id,'type':'markdown','body':body}
        if source_id: block['sourceId']=source_id
        blocks.append(block)
    datasets, tables, charts = {}, [], []
    def table(id,title,rows,labels,source_id='shadow'):
        datasets[id]=rows
        # Exact audit lookup stays a semantic markdown table: this renderer's
        # native table contract only accepts SQL, whereas these results are Python.
        body='### '+title+'\n\n| '+' | '.join(label for _,label in labels)+' |\n'
        body+='| '+' | '.join('---' for _ in labels)+' |\n'
        body+='\n'.join('| '+' | '.join(str(row[field]).replace('|','\\|') for field,_ in labels)+' |' for row in rows)
        section(id+'-block',body,source_id)

    title='FY27 学校版 Excel 影子核对'
    section('title',f'# {title}\n\n本地只读核对 · 2026-09-06 · 状态：部分核对完成，整表系统重算与入库尚未通过')
    section('summary','## 算术复算通过，完整业务口径尚未对齐\n\n'
        '覆盖 238 个预算拆解单元、952 个季度明细。关键参数、人数、收入与结转共 13,693 项核对无差异；'
        '这不是全工作簿所有公式的验收，也不是正式发布通过。\n\n'
        '将 Excel 人数直接交给收入内核后，可接受的 948 个季度明细共 5,688 项金额比较通过；'
        '最大绝对差异 0.000000925 元。第 151 行因期初结转 −0.01 元，四季度均未纳入该内核子集。'
        '全表收入内核实际调用返回 NEGATIVE_REVENUE_INPUT，未将负数改成零。','shadow')
    section('income','## 收入接近学校目标，但不能据此判定可发布\n\n'
        '以下为全年税后结转，单位元；差额 = Excel − 通知目标。目标来自用户提供的首轮预算通知。'
        '没有更晚目标版本的证据，本轮只作该版本对照，不改动目标。')
    table('income-table','学校版年度收入核对',[
        {'scope':r['scope'],'excel':number(r['excel_yuan']),'target':number(r['target_yuan']),
         'delta':number(r['difference_yuan'])} for r in summary['annual_targets']],
        [('scope','范围'),('excel','Excel（元）'),('target','通知目标（元）'),('delta','差额（元）')])
    section('discount','## 15 个综合折扣目标均超出已确认容差\n\n'
        '按全表“折后业务收入合计 ÷ 原价业务收入合计”计算，不平均明细折扣，也不以税后结转作权重。'
        '部门全年为 87.2078%，通知目标 86%，差 1.2078 个百分点。'
        '达标条件仍是绝对差 ≤ 0.05 个百分点；显示四舍五入不能代替精确判定。'
        '明细折扣本身符合 0.1 个百分点步长。')
    table('discount-table','综合折扣对照（全表，不排除第 151 行）',[
        {'scope':r['scope'],'season':r['season'],'actual':number(r['actual_percent'],4)+'%',
         'target':number(r['target_percent'],2)+'%','delta':number(r['difference_percentage_points'],4),
         'status':'通过' if r['within_0_05pp'] else '超出容差'} for r in summary['discount_targets']],
        [('scope','范围'),('season','期间'),('actual','Excel 加权值'),('target','通知目标'),('delta','差异（百分点）'),('status','判定')])
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory=sqlite3.Row
        datasets['department-discount-gap']=[dict(r) for r in connection.execute(chart_query,
            {'source_json':(OUT/'source-cells.json').read_text()})]
    for row, baseline in zip(datasets['department-discount-gap'],summary['discount_targets'][:5],strict=True):
        assert row['period']==baseline['season']
        assert abs(Decimal(str(row['gap_pp']))-Decimal(baseline['difference_percentage_points'])) < Decimal('1e-10')
    charts.append({'id':'department-gap','type':'bar','title':'部门各期间综合折扣偏差',
        'subtitle':'Excel 加权率减通知目标；单位：百分点；达标区间为 −0.05 至 +0.05。全年与四季是独立判定范围，并非时间趋势。',
        'dataset':'department-discount-gap','sourceId':'discount-query','valueFormat':'number',
        'encodings':{'x':{'field':'period','type':'nominal','label':'判定期间'},
                     'y':{'field':'gap_pp','type':'quantitative','label':'偏差（百分点）'},
                     'tooltip':[{'field':'actual_percent','type':'quantitative','label':'Excel（%）','format':'number'},
                                {'field':'target_percent','type':'quantitative','label':'目标（%）','format':'number'}]}})
    blocks.append({'id':'department-gap-block','type':'chart','chartId':'department-gap'})
    section('blockers','## 先补齐期初老生和输入状态，再开展全链路比对\n\n'
        '系统首季老生固定为零，而 Excel 有 77 个单元、3,876 老生人次，由 FY26 春人次及春续暑率得到。'
        '现有接口没有对应期初老生输入，不能通过增加入口或计划新生替代。\n\n'
        '不虚构人工计划或确认状态运行人数内核，得到 138 项缺少锁定计划新生、714 项上游不可用；'
        '受分组取整完整性影响，952 个季度明细均未形成完整人数结果。这不表示系统收入为零，而是当前不可计算。','shadow')
    finding_rows=[
        {'item':'期初老生','evidence':'77 单元 / 3,876 人次','impact':'源 Excel 与系统首季起点不同','action':'补齐版本化期初老生输入设计，业务确认后实施'},
        {'item':'最低班均补人','evidence':'183 明细季度 / 当地增量合计 834 人次','impact':'Excel 自动增加新生；系统班均只校验','action':'保留已确认的系统规则，单独评估差异'},
        {'item':'负数期初结转','evidence':'滚动表 AG151 = −0.01 元','impact':'整表收入内核拒绝输入','action':'由数据维护人员判定来源或更正，不自动截断'},
        {'item':'空值与缺失','evidence':'27 个入口查找缺失；105 次比例查找命中空值','impact':'Excel 变成 0；不等于明确确认的零','action':'区分不启用、明确零、缺少数据，不自动锁定'},
        {'item':'全年零与不足班均','evidence':'61 个全年零单元；3 个季度不足班均','impact':'如全部启用，不能通过现行校验','action':'数据维护后由有权限的负责人明确启用状态'},
        {'item':'取整及参数精度','evidence':'逐行 ROUND 与分组最大余数不同；242 个比例键需 ppm 舍入','impact':'可能产生后续传导差异，本轮未孤立量化','action':'不把 Excel 逐行取整改为系统既定规则'},
    ]
    section('blocker-details','## 差异与处理边界\n\n'+'\n\n'.join(
        f'### {r["item"]}\n\n{r["evidence"]}。{r["impact"]}。\n\n建议：{r["action"]}。' for r in finding_rows),'shadow')
    section('topup-note','## Excel 的补人增量不能当作全年净影响\n\n'
        '834 为各受影响单元在各季度，Excel 实际新生减去“本季已取整老生 × 新生占比 ÷ 老生占比，再四舍五入”的局部差额之和。'
        '它包含季度传导后的状态，不是取消补人后的全年净减少人数，也不是独立人数。以下为按行号、季度排序的前 10 条证据。','shadow')
    import csv
    with (OUT/'excel-topups.csv').open(encoding='utf-8-sig') as f:
        topups=list(csv.DictReader(f))
    table('topup-table','补人明细前 10 条',[
        {k:r[k] for k in ('cell','season','campus','old','natural_new','excel_new','local_topup')} for r in topups[:10]],
        [('cell','源单元格'),('old','老生'),('natural_new','比例新生'),('excel_new','Excel 新生'),('local_topup','补足')])
    section('scope','## 入口合计一致，但“入口”不等于“七年级入口”\n\n'
        '完整维度为财季＋赛道＋产品＋年级＋科目＋校区；本表未发现重复键或必填维度缺失。'
        '入口属性为“是”的暑假新生共 8,007 人次，与参数表入口合计一致；其中七年级 7,700、八年级 163、九年级 144。'
        '透视表的 7,700 对应七年级入口，不应和含例外的 8,007 强行对齐。'
        '参数表 F1 的“七年级 10000 人次”标题与当前数值不一致，不能将标题当作已锁定总值。','shadow')
    section('method','## 三层核对各有独立边界\n\n'
        '1. 原文件只读，分别读取公式与 OOXML 保存值；核对前后验证 SHA-256。整列查找按所在行的精确键复算，不把通用导入器的兼容性错误当作 Excel 错误。\n'
        '2. 白名单复算核心公式：参数精确查找、春续暑、逐行人数取整、班均补人、价格折扣、四季结转及全年合计。人数零容差，金额每项 0.01 元；该金额只是审计比较容差，不是正式发布容差。\n'
        '3. 使用仓库真实人数、班均及收入纯计算内核。收入子集沿用 Excel 人数，不能证明系统人数推导已对平；负数行不进入子集，但仍保留在 Excel 全表及目标检查中。'
        '比例空值不映射成已确认参数，在线计划与来源批次不伪造。\n\n'
        '复跑入口：analysis/fy27_shadow/extract.py → reconcile.py → build_report.py。逐项差异见同目录 CSV，代码哈希见 summary.json。','shadow')
    section('limitations','## 数据来源与尚未验证事项\n\n'
        f'源 SHA-256 前 16 位：{summary["source_sha256"][:16]}；完整值保存在 summary.json。\n\n'
        '文件属性的修改时间不是业务快照日期。本轮未取得 BI 来源批次、业务快照日期、完整参数锁定与确认记录，'
        '因此未冻结正式来源、未创建正式参数版本、未运行数据库/API 全链路、未发布或自动调平。'
        '“无保存值错误”只指滚动表与参数表，不能推广到其他工作表。\n\n'
        '精确数值表是本次审计的主要证据，偏差柱图辅助比较部门各期与目标的差距，不作趋势推断。笔记本伴随文件用同一 Python 模块顺序复跑；'
        '环境未安装 Jupyter/nbformat，未做 Jupyter 内核或 UI 验证。\n\n'
        '预览限制：共享阅读器页眉使用视口宽度，在传统滚动条模式出现约 8 像素横向溢出，浏览器自动验收未通过；'
        '正文与数据未被截断，默认覆盖式滚动条模式未见该溢出。HTML 结构验证已通过，本轮未修改共享阅读器。','shadow')
    section('next','## 下一步建议\n\n'
        '优先补齐“版本化期初老生”纵切：明确上一财年春人次、春续暑率与年级映射的输入及留痕；'
        '保留已确认的同预算单元续班去向规则，不提前引入后续季度迁移。'
        '随后处理 −0.01 元、缺失/零值与启用状态，再运行真实快照到修订的全链路影子对照。'
        '不以修改续班率、新生占比或折扣来掩盖模型差异。')
    section('question','## 待确认事项\n\n'
        '建议先确认期初老生的输入方式：是否以 Excel 的 FY26 春人次及春续暑率为初始依据，'
        '将其作为独立版本化输入纳入系统？本报告尚未改变现有业务规则或接口。')
    sources=[source,derived,targets,chart_source]
    artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':title,
        'description':'真实 Excel 与预算内核的离线、只读、可追溯核对；整表导入阻断。',
        'generatedAt':summary['generated_at'],'blocks':blocks,'cards':[],'charts':charts,'tables':tables,'sources':sources},
        'snapshot':{'version':1,'generatedAt':summary['generated_at'],'status':'partial','datasets':datasets,
            'accessIssues':[{'id':'missing-input-governance','dataset':'source-governance',
                'message':'缺少业务快照日期、来源批次与已确认人工计划；完整系统重算和正式入库尚未通过。'}]},'sources':sources}
    write_json(OUT/'artifact.json',artifact)
    build_notebook(summary)
    print('Created artifact.json and reconciliation.ipynb')


def build_notebook(summary):
    # Notebook packages are unavailable. Use standard v4 JSON and execute plain
    # Python cells in order; disclose this instead of claiming Jupyter execution.
    cells=[]
    def md(text): cells.append({'cell_type':'markdown','metadata':{},'source':text.splitlines(True)})
    def code(text): cells.append({'cell_type':'code','metadata':{},'source':text.splitlines(True),
                                 'execution_count':None,'outputs':[]})
    md('# FY27 学校版 Excel 影子核对\n\n## tl;dr\n\n核心算术复算无差异；完整人数链及正式入库仍阻断。收入内核子集通过不能代替完整业务验收。')
    md('## Context & Methods\n\n只读原 Excel 保存值及公式，以真实纯计算内核对照，不改 Excel、不写数据库。\n\n'
       '### Key Assumptions\n\n来源为固定 SHA-256 的本地备份，不是已确认 BI 快照。金额比较容差 0.01 元、人数零容差。'
       '缺失人工计划不自动填零。复跑时从项目根目录或 output/fy27-shadow 目录打开。')
    code('from pathlib import Path\nimport json, hashlib, sys\n'
         'root = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "analysis/fy27_shadow/reconcile.py").exists())\n'
         'sys.path.insert(0, str(root))\nfrom analysis.fy27_shadow.reconcile import audit\n')
    md('## Data\n\n滚动表第 2–239 行、参数表六个主计算参数区域；业务快照日期和来源批次尚缺。')
    code('payload = json.loads((root / "output/fy27-shadow/source-cells.json").read_text())\n'
         'assert hashlib.sha256((root / payload["source_file"]).read_bytes()).hexdigest() == payload["sha256"]\n'
         'summary, evidence = audit(payload)\nprint(payload["source_file"], payload["sha256"])\n'
         'print(json.dumps(evidence["normalized-lines"][:10], ensure_ascii=False, indent=2, default=str))\n')
    md('## Results\n\n先检查公式与金额一致性，再查看无法计算或禁止导入的项。')
    code('assert summary["formula_failures"] == 0\nassert summary["kernel_revenue_failures"] == 0\n'
         'assert summary["strict_headcount_publishable"] is False\n'
         'keys = ["annual_targets", "opening_old_students", "topup_row_seasons", "full_revenue_kernel_error", '
         '"kernel_revenue_rows", "strict_headcount_validation_codes", "same_input_class_size_validation_codes"]\n'
         'print(json.dumps({k:summary[k] for k in keys}, ensure_ascii=False, indent=2, default=str))\n')
    md('## Takeaways\n\n下一步先明确并补齐版本化期初老生，再处理负数结转、零值/缺失状态和参数确认。'
       '不把局部補人 834 人次解读为取消补人后的全年净减少。\n\n'
       '执行说明：本次由 Python 顺序执行所有普通代码单元；环境无 nbformat/nbclient/ipykernel，未验证 Jupyter 内核。')
    state={'__name__':'__main__'}
    count=0
    for i,cell in enumerate(cells):
        cell['id']=f'fy27-{i+1}'
        if cell['cell_type']=='code':
            count+=1
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream): exec(compile(''.join(cell['source']),f'notebook-cell-{i}','exec'),state)
            cell['execution_count']=count
            cell['outputs']=[{'output_type':'stream','name':'stdout','text':stream.getvalue().splitlines(True)}]
    notebook={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},
        'language_info':{'name':'python'},'validation':{'plain_python_sequential_execution':'passed','jupyter_kernel':'not_run_dependencies_missing'}},'cells':cells}
    write_json(OUT/'reconciliation.ipynb',notebook)


if __name__=='__main__': main()
