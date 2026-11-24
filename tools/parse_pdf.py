# -*- coding: utf-8 -*-
"""从两份 PDF 题库生成 data/bank.js

用法：python tools/parse_pdf.py
输入：《综合化学》.pdf、化工原理.pdf（仓库根目录）
输出：data/bank.js、data/bank.json、tools/parse-report.txt

两份 PDF 格式不同：
  · 综合化学：题干里带答案括号（ A ），选项同一行用空格分隔，简答/计算用「答：」
  · 化工原理：选项一行一个，答案在选项后单独一行「答案：A」/「正确答案： 错误」
"""
import re, json, os, sys, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
OUT_JSON = os.path.join(BASE, 'data', 'bank.json')
OUT_JS = os.path.join(BASE, 'data', 'bank.js')
REPORT = os.path.join(HERE, 'parse-report.txt')

SOURCES = [
    ('综合化学', os.path.join(BASE, '《综合化学》.pdf')),
    ('化工原理', os.path.join(BASE, '化工原理.pdf')),
]

CN = '一二三四五六七八九十'
PAGE_RE = re.compile(r'^\s*=== PAGE \d+ ===\s*$')
CHAPTER_RE = re.compile(r'^《(.+?)》\s*(\d+)?\s*题\s*$')
SECTION_RE = re.compile(r'^([' + CN + r']+)\s*[、.]\s*(.+)$')
QNUM_RE = re.compile(r'^(\d{1,3})\s*[、.]\s*(.*)$')
ANS_LINE_RE = re.compile(r'^(答案|正确答案)\s*[：:]\s*(.*)$')
PARSE_LINE_RE = re.compile(r'^(解析|分析)\s*[：:]\s*')
ANS_TEXT_RE = re.compile(r'^答\s*[：:]\s*(.*)$')
A_LETTERS = re.compile(r'[（(]\s*([A-H](?:\s*[A-H]){0,7})\s*[）)]')
A_JUDGE = re.compile(r'[（(]\s*(√|×|✓|✗|正确|错误|对|错)\s*[）)]')
OPT_A = re.compile(r'(?<![A-Za-z0-9])([A-H])\s*[、.．]\s*')

report = []
def log(*a):
    report.append(' '.join(str(x) for x in a))

# ---------- 文本清洗 ----------
# PDF 里有些符号来自 Symbol 字体，被映射到了私用区（U+E000–U+F8FF），
# 任何字体都画不出来，浏览器会显示成方块。按 Symbol 字体表还原成真正的字符。
PUA_MAP = {
    '\uf0b4': '×',   # multiply
    '\uf0d7': '·',   # dotmath（单位中间点）
    '\uf03d': '=',   # equals
    '\uf02d': '-',   # minus
    '\uf02b': '+',   # plus
    '\uf0ae': '→',   # arrowright
    '\uf0ac': '←',   # arrowleft
    '\uf0be': '',    # 箭头残片，丢弃
    '\uf044': 'Δ',   # Delta（Symbol 的 D）
    '\uf070': 'π',   # pi（Symbol 的 p）
}

def fix_pua(s):
    if not s:
        return s
    if not any('\ue000' <= ch <= '\uf8ff' for ch in s):
        return s
    out = []
    for ch in s:
        if ch in PUA_MAP:
            out.append(PUA_MAP[ch])
        elif '\ue000' <= ch <= '\uf8ff':
            continue
        else:
            out.append(ch)
    return ''.join(out)
def strip_cjk_space(s):
    """去掉汉字/中文标点之间的空格（PDF 两端对齐会产生这种空格）"""
    return re.sub(r'(?<=[^\x00-\x7f])[ \t]+(?=[^\x00-\x7f])', '', s)

def norm(s):
    s = fix_pua(s)
    s = s.replace('\u3000', ' ')
    s = re.sub(r'[ \t]{2,}', ' ', s)
    return s.strip()

def join_piece(a, b):
    """续行拼接：中文直接连，英文/数字之间留空格"""
    if not a:
        return b
    if not b:
        return a
    if re.search(r'[A-Za-z0-9]$', a) and re.match(r'[A-Za-z0-9]', b):
        return a + ' ' + b
    return a + b

NEW_BLOCK_RE = re.compile(r'^(?:[（(]?\d+[)）.、]|[①②③④⑤⑥⑦⑧⑨⑩]|[A-H][、.]|[-•·])')

SOLUTION_MARK = re.compile(r'(?:解答|解)\s*[:：]|解\s*[（(]\s*1\s*[)）]')
BOUNDARY = '。！？；：\n）)'

def split_solution(text):
    """把「解答：/解：」之后的内容从题干里切出来，返回 (题干, 解答)"""
    for m in SOLUTION_MARK.finditer(text):
        i = m.start()
        if i > 0 and text[i - 1] not in BOUNDARY:
            continue
        sol = SOLUTION_MARK.sub('', text[i:], count=1).strip()
        head = text[:i].strip()
        if head and sol:
            return head, sol
    return None

# 原文本身有问题、需要人工说明的题目（键：科目/章节#原题号）
MANUAL_NOTES = {
    '化工原理/化工原理#29': '题干中的乙醇-水气液平衡数据表在原文档里是图片，网页版未收录，'
                   '请对照《化工原理》.pdf 第 73 页作图。',
    '化工原理/化工原理#32': '解答中的乘号、点号在文本转换时丢失，例如「1/210000.25」实际是「1/2×1000×0.25」，'
                   '请对照《化工原理》.pdf 第 75–76 页。',
    '化工原理/化工原理#33': '原文档只给出题目，没有给出参考答案。',
    '化工原理/化工原理#34': '解答在原文档中是竖排公式（分数上下叠放），转成文字后运算符号与上下标会丢失，'
                   '请对照《化工原理》.pdf 第 77 页。',
    '化工原理/化工原理#35': '解答中的乘号、点号与上下标在文本转换时丢失，请对照《化工原理》.pdf 第 77 页。',
    '综合化学/有机化学#95': '原文档未给出本题的参考答案。',
}

# 原文排版已经坏到无法还原的题目（键：科目/章节#原题号）
MANUAL_INVALID = {
    '化工原理/化工原理#146': '原文把题干和选项排在同一行，答案还给成一串批量答案（解析：AABCBBCDCADDA），无法可靠还原',
}

def append_answer(q, text):
    """把一行答案并入答案块：句子没说完就连起来，另起一段才换行"""
    lines = q['ans_lines']
    if not lines:
        lines.append(text)
        return
    prev = lines[-1]
    if NEW_BLOCK_RE.match(text) or re.search(r'[。；！？]$', prev):
        lines.append(text)
    else:
        lines[-1] = join_piece(prev, text)

# ---------- 章节 / 题型 ----------
def section_type(text):
    t = text.replace(' ', '')
    if '多项选择' in t:
        return 'multi'
    if '单项选择' in t or '单选题' in t:
        return 'single'
    if '判断' in t:
        return 'judge'
    if '计算' in t:
        return 'calc'
    if '简答' in t:
        return 'short'
    if '综合' in t or '问答' in t or '论述' in t:
        return 'essay'
    return None

KIND_TYPE = {'single': 'single', 'multi': 'multi', 'judge': 'judge',
             'short': 'essay', 'calc': 'essay', 'essay': 'essay'}
KIND_NAME = {'single': '单选题', 'multi': '多选题', 'judge': '判断题',
             'short': '简答题', 'calc': '计算题', 'essay': '综合题'}

# ---------- 单份文档解析 ----------
def parse_doc(path, subject_name, id_prefix='c'):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit('缺少 pypdf，请先执行：python -m pip install pypdf')

    reader = PdfReader(path)
    lines = []
    page_no = 0
    for pi, page in enumerate(reader.pages, start=1):
        txt = page.extract_text() or ''
        page_no = pi
        for ln in txt.splitlines():
            if not ln or not ln.strip():
                continue
            if PAGE_RE.match(ln):
                continue
            s = strip_cjk_space(norm(ln))
            if s:
                lines.append((page_no, s))

    chapters, sections, questions = [], [], []
    cur_chapter = None
    cur_section = None
    cur = None
    last_no = 0

    def chapter(name):
        nonlocal cur_chapter
        cid = '%s%d' % (id_prefix, len(chapters) + 1)
        cur_chapter = {'id': cid, 'name': name, 'subjectId': subject_name}
        chapters.append(cur_chapter)

    def finish():
        nonlocal cur
        if not cur:
            return
        q, cur = cur, None
        stem = q['stem'].strip()
        stem = re.sub(r'\s{2,}', ' ', stem)
        issues = []
        kind = q['kind']
        qtype = KIND_TYPE[kind]
        answer = []
        answer_line = q.get('answer_line', '')

        # 1) 题干里的答案括号
        if qtype in ('single', 'multi'):
            m = None
            for mm in A_LETTERS.finditer(stem):
                m = mm
            if m:
                answer = list(re.sub(r'\s', '', m.group(1)))
                stem = stem[:m.start()] + '（  ）' + stem[m.end():]
        elif qtype == 'judge':
            m = A_JUDGE.search(stem)
            if m:
                c = m.group(1)
                answer = ['对'] if c in ('√', '✓', '正确', '对') else ['错']
                stem = (stem[:m.start()] + '（  ）' + stem[m.end():]).strip()

        # 2) 「答案：A」/「正确答案： 错误」单独一行
        if not answer and answer_line:
            seg = answer_line.strip()
            if qtype == 'judge':
                if '正确' in seg or '对' in seg or '√' in seg:
                    answer = ['对']
                elif '错误' in seg or '错' in seg or '×' in seg:
                    answer = ['错']
            else:
                letters = re.findall(r'[A-H]', seg.upper())
                if letters and len(seg) <= 12:
                    answer = sorted(set(letters))

        if qtype == 'judge':
            stem = re.sub(r'[（(]\s*[）)]', '（  ）', stem)

        # 题干里混进的选项字母（如「平均流速u C、的关系」「内径为D、/2」）去掉多余顿号
        stem = re.sub(r'([A-Za-z])\s*、(?=[，,/／的关])', r'\1', stem)

        opts, seen = [], set()
        for k, t in q['options']:
            t = t.strip()
            if not t or k in seen:
                continue
            seen.add(k)
            opts.append({'k': k, 't': t})
        opts.sort(key=lambda o: o['k'])

        if qtype in ('single', 'multi'):
            if len(opts) < 2:
                issues.append('NO_OPTIONS')
            if not answer:
                issues.append('NO_ANSWER')
            if answer and any(a not in seen for a in answer):
                issues.append('ANSWER_NOT_IN_OPTIONS')
            if qtype == 'single' and len(answer) > 1:
                kind = 'multi'
                qtype = 'multi'
            if qtype == 'multi' and len(answer) == 1:
                kind = 'single'
                qtype = 'single'
        if qtype == 'judge' and not answer:
            issues.append('NO_ANSWER')

        analysis = '\n'.join(x.strip() for x in q['ans_lines'] if x.strip())

        # 有些计算题的「解答」直接接在题干后面，没有单独的答案行。
        # 只在没抓到答案时才尝试拆，避免误伤正常题目。
        if qtype == 'essay' and not analysis:
            parts = split_solution(stem)
            if parts:
                stem, analysis = parts
                issues.append('SOLUTION_IN_STEM')

        # 项目符号单独成行，题干更好读
        if qtype == 'essay' and '•' in stem:
            stem = re.sub(r'\s*•\s*', '\n• ', stem).strip()

        # 人工说明只针对主观题（题号在每个大题里都会从 1 开始，必须限定题型）
        note = MANUAL_NOTES.get('%s/%s#%s' % (subject_name, q['chapter'], q['no'])) if qtype == 'essay' else None
        if note:
            analysis = (analysis + '\n' if analysis else '') + '注：' + note
        if qtype == 'essay' and not analysis:
            issues.append('NO_ANALYSIS')

        valid = True
        if qtype in ('single', 'multi'):
            if len(opts) < 2 or not answer or any(a not in seen for a in answer):
                valid = False
            # 题干只剩「是（）」「的名称是」这种，说明题目主体是图片
            bare = re.sub(r'[\s（）()，。、？?：:；;]', '', stem)
            if len(bare) < 5:
                issues.append('STEM_EMPTY')
                valid = False
        elif qtype == 'judge':
            if not answer:
                valid = False

        mkey = '%s/%s#%s' % (subject_name, q['chapter'], q['no'])
        if mkey in MANUAL_INVALID and qtype in ('single', 'multi'):
            issues.append('MANUAL_INVALID')
            valid = False

        questions.append({
            'subjectId': subject_name,
            'chapterId': q['chapterId'],
            'chapter': q['chapter'],
            'section': q['section'],
            'page': q.get('page', 0),
            'type': qtype,
            'kind': kind,
            'level': '',
            'no': q['no'],
            'stem': stem,
            'options': opts,
            'answer': answer,
            'analysis': analysis,
            'issues': issues,
            'valid': valid,
        })

    for page_no, line in lines:
        # 章节
        mc = CHAPTER_RE.match(line)
        if mc:
            finish()
            chapter(mc.group(1).strip())
            cur_section = None
            last_no = 0
            continue

        # 小节
        ms = SECTION_RE.match(line)
        if ms and len(line) < 40:
            st = section_type(ms.group(2))
            if st:
                finish()
                if cur_chapter is None:
                    chapter(subject_name)
                cur_section = {'name': ms.group(1) + '、' + ms.group(2).strip(), 'kind': st}
                sections.append({'name': cur_section['name'], 'kind': st,
                                 'subjectId': subject_name, 'chapterId': cur_chapter['id']})
                last_no = 0
                continue

        if cur_section is None:
            continue

        # 新题
        mq = QNUM_RE.match(line)
        if not mq:
            # 少数题号后面没有顿号，如「97 系统命名法命名…」，只在顺号时才认
            mq2 = re.match(r'^(\d{1,3})\s+(\S.*)$', line)
            if mq2 and cur is not None and int(mq2.group(1)) == last_no + 1:
                mq = mq2
                log('  题号无顿号已修正：%s' % line[:32])
        if mq:
            no = int(mq.group(1))
            if no in (last_no + 1, last_no) or cur is None:
                finish()
                if cur_chapter is None:
                    chapter(subject_name)
                cur = {
                    'no': no, 'kind': cur_section['kind'], 'chapterId': cur_chapter['id'],
                    'chapter': cur_chapter['name'], 'section': cur_section['name'],
                    'stem': mq.group(2).strip(), 'options': [], 'ans_lines': [], 'page': page_no,
                    'answer_line': '', 'started': False, 'parse_started': False
                }
                last_no = no
                continue

        if cur is None:
            continue

        kind = cur['kind']
        is_essay = kind in ('short', 'calc', 'essay')

        # 答案行
        m = ANS_LINE_RE.match(line)
        if m:
            body = m.group(2).strip()
            if is_essay:
                cur['started'] = True
                if body:
                    append_answer(cur, body)
            else:
                cur['answer_line'] = body
            continue

        # 「解析：」——选择题、判断题也会有，单独收进解析
        mp = PARSE_LINE_RE.match(line)
        if mp:
            body = line[mp.end():].strip()
            if body:
                cur['ans_lines'].append(body)
            cur['parse_started'] = True
            continue
        if cur.get('parse_started') and not is_essay:
            if cur['ans_lines']:
                cur['ans_lines'][-1] = join_piece(cur['ans_lines'][-1], line)
            else:
                cur['ans_lines'].append(line)
            continue
        if is_essay:
            m2 = ANS_TEXT_RE.match(line)
            if m2:
                cur['started'] = True
                body = m2.group(1).strip()
                if body:
                    append_answer(cur, body)
                continue
            if cur['started']:
                append_answer(cur, line)
            else:
                cur['stem'] = join_piece(cur['stem'], line)
            continue

        # 选项
        marks = [(mm.start(), mm.group(1), mm.end()) for mm in OPT_A.finditer(line)]
        if marks and len(marks) >= 1:
            marks = [mk for mk in marks if mk[1] not in {o[0] for o in cur['options']}]
            if marks:
                for i, (pos, letter, end) in enumerate(marks):
                    nxt = marks[i + 1][0] if i + 1 < len(marks) else len(line)
                    cur['options'].append((letter, line[end:nxt].strip()))
                continue

        # 续行
        if cur['options']:
            k, t = cur['options'][-1]
            cur['options'][-1] = (k, join_piece(t, line))
        else:
            cur['stem'] = join_piece(cur['stem'], line)

    finish()
    return chapters, sections, questions

# ---------- 主流程 ----------
def main():
    all_chapters, all_sections, all_questions = [], [], []
    subjects = []
    for idx, (name, path) in enumerate(SOURCES):
        if not os.path.exists(path):
            raise SystemExit('找不到文件：' + path)
        subjects.append({'id': name, 'name': name})
        chs, secs, qs = parse_doc(path, name, 'c%d-' % (idx + 1))
        log('%s：%d 章 / %d 小节 / %d 题' % (name, len(chs), len(secs), len(qs)))
        all_chapters.extend(chs)
        all_sections.extend(secs)
        all_questions.extend(qs)

    # 全局 id
    for i, q in enumerate(all_questions):
        q['id'] = 'q%05d' % (i + 1)

    # 有些题的选项是 PDF 里画出来的结构式，纯文本抓不到；
    # 这些图已逐页核对渲染页后提取到 data/img，这里按题号挂回去。
    IMG_PATCH = {
        'q00411': {'opts': {'A': 'q00411_A.jpg', 'B': 'q00411_B.jpg', 'C': 'q00411_C.jpg', 'D': 'q00411_D.jpg'}},
        'q00412': {'opts': {'A': 'q00412_A.jpg', 'B': 'q00412_B.jpg', 'C': 'q00412_C.jpg'}},
        'q00413': {'stem': 'q00413_stem.jpg'},
        'q00418': {'opts': {'A': 'q00418_A.jpg', 'B': 'q00418_B.jpg', 'C': 'q00418_C.jpg'}},
        'q00432': {'stem': 'q00432_stem.jpg'},
        'q00436': {'opts': {'A': 'q00436_A.jpg', 'B': 'q00436_B.jpg', 'C': 'q00436_C.jpg'}},
        'q00443': {'opts': {'A': 'q00443_A.jpg', 'B': 'q00443_B.jpg', 'C': 'q00443_C.jpg'}},
        'q00450': {'stem': 'q00450_stem.jpg', 'opts': {'A': 'q00450_A.jpg', 'B': 'q00450_B.jpg', 'C': 'q00450_C.jpg'}},
        'q00455': {'opts': {'A': 'q00455_A.jpg', 'B': 'q00455_B.jpg', 'C': 'q00455_C.jpg'}},
        'q00461': {'opts': {'A': 'q00461_A.jpg', 'B': 'q00461_B.jpg', 'C': 'q00461_C.jpg'}},
        'q00462': {'opts': {'A': 'q00462_A.jpg', 'B': 'q00462_B.jpg', 'C': 'q00462_C.jpg'}},
        'q00463': {'opts': {'A': 'q00463_A.jpg', 'B': 'q00463_B.jpg', 'C': 'q00463_C.jpg'}},
    }
    patched = 0
    for q in all_questions:
        patch = IMG_PATCH.get(q['id'])
        if not patch:
            continue
        if patch.get('stem'):
            q['stemImg'] = 'data/img/' + patch['stem']
            q['issues'] = [i for i in q['issues'] if i != 'STEM_EMPTY']
            if len(q['options']) >= 2 and q['answer']:
                q['valid'] = True
                patched += 1
        if patch.get('opts'):
            opts = [{'k': k, 't': '', 'img': 'data/img/' + fn}
                    for k, fn in sorted(patch['opts'].items())]
            for o in q['options']:
                if o['k'] not in patch['opts']:
                    opts.append(o)
            opts.sort(key=lambda x: x['k'])
            q['options'] = opts
            q['valid'] = True
            q['issues'] = [i for i in q['issues']
                           if i not in ('NO_OPTIONS', 'ANSWER_NOT_IN_OPTIONS')]
            patched += 1
    log('')
    log('图片题已补回：%d 道' % patched)

    # 统计
    kinds = collections.Counter(q['kind'] for q in all_questions)
    types = collections.Counter(q['type'] for q in all_questions)
    bad = [q for q in all_questions if q['issues']]
    log('')
    log('题型分布(交互类型)：', dict(types))
    log('题型分布(试卷分类)：', dict(kinds))
    log('可用题数：%d / %d' % (sum(1 for q in all_questions if q['valid']), len(all_questions)))
    log('异常题数：%d' % len(bad))
    log('')
    log('=== 科目 / 章节 ===')
    for s in subjects:
        qs = [q for q in all_questions if q['subjectId'] == s['id']]
        log('%-8s 共 %d 题' % (s['id'], len(qs)))
        for c in all_chapters:
            if c['subjectId'] != s['id']:
                continue
            cq = [q for q in qs if q['chapterId'] == c['id']]
            detail = collections.Counter(q['kind'] for q in cq)
            log('    %-10s %4d 题  %s' % (c['name'], len(cq), dict(detail)))
    log('')
    log('=== 异常明细（前 40 条）===')
    for q in bad[:40]:
        log('%s [%s] %s | %s' % (q['id'], ','.join(q['issues']), q['chapter'], q['stem'][:60]))

    # 不可用题目：按「来源文件 + 原因」分组，展示在网站「关于」里
    excluded = []
    unusable = [q for q in all_questions if not q['valid']]
    if unusable:
        SRC = {name: os.path.basename(path) for name, path in SOURCES}

        def category(q):
            for i in q['issues']:
                if i in ('NO_OPTIONS', 'ANSWER_NOT_IN_OPTIONS'):
                    return '原文选项是化学结构式插图，纯文本无法还原，因此无可作答的选项'
                if i == 'STEM_EMPTY':
                    return '原文题干本身是化学结构图，纯文本只剩几个字，无法还原'
                if i == 'MANUAL_INVALID':
                    return '原文排版损坏（题干与选项混排，答案还给成了一串批量答案），无法可靠还原'
            return '题目信息不完整'

        groups = {}
        for q in unusable:
            key = (SRC.get(q['subjectId'], ''), category(q))
            g = groups.get(key)
            if not g:
                g = groups[key] = {
                    'source': key[0], 'reason': key[1],
                    'pages': [], 'chapters': set(), 'kinds': set(), 'numbers': []
                }
            if q.get('page'):
                g['pages'].append(q['page'])
            g['chapters'].add(q['chapter'])
            g['kinds'].add(KIND_NAME[q['kind']])
            g['numbers'].append(q['no'])
        for g in groups.values():
            ps = sorted(set(g['pages']))
            if ps:
                g['pages'] = ('第 %d 页' % ps[0]) if ps[0] == ps[-1] else ('第 %d–%d 页' % (ps[0], ps[-1]))
            else:
                g['pages'] = ''
            g['chapters'] = sorted(g['chapters'])
            g['kinds'] = sorted(g['kinds'])
            g['numbers'] = sorted(g['numbers'])
            g['count'] = len(g['numbers'])
            excluded.append(g)
        excluded.sort(key=lambda x: (x['source'], x['reason']))
        log('')
        log('=== 不可用题目备注 ===')
        log('合计 %d 道' % sum(g['count'] for g in excluded))
        for g in excluded:
            log('  [%d 道] %s %s | %s | %s' % (g['count'], g['source'], g['pages'],
                                              g['reason'], '、'.join(g['chapters'])))
            log('          原题号：%s' % '、'.join(str(n) for n in g['numbers']))

    # 页码只在备注里用，不写进每题数据
    for q in all_questions:
        q.pop('page', None)

    # 答案里带「注：」的题目（原文公式/图片无法完整还原）
    noted = [q for q in all_questions if q.get('analysis') and ('\n注：' in q['analysis'] or q['analysis'].startswith('注：'))]
    formula_notes = None
    if noted:
        formula_notes = {
            'count': len(noted),
            'numbers': sorted({q['no'] for q in noted}),
            'chapters': sorted({q['chapter'] for q in noted}),
        }
        log('')
        log('=== 带说明的题目（原文公式/图片）===')
        log('数量：%d　题号：%s' % (formula_notes['count'],
                                  '、'.join(str(n) for n in formula_notes['numbers'])))

    meta = {
        'title': '智题题库',
        'source': '《综合化学》.pdf · 化工原理.pdf',
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'count': len(all_questions),
        'validCount': sum(1 for q in all_questions if q['valid']),
        'excluded': excluded,
        'formulaNotes': formula_notes,
    }
    data = {'meta': meta, 'subjects': subjects, 'chapters': all_chapters,
            'sections': all_sections, 'questions': all_questions}

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    txt = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    open(OUT_JSON, 'w', encoding='utf-8').write(txt)
    open(OUT_JS, 'w', encoding='utf-8').write(
        '/* 题库数据：由 tools/parse_pdf.py 从 PDF 自动生成，请勿手工修改 */\n'
        'window.QBANK = ' + txt + ';\n')
    open(REPORT, 'w', encoding='utf-8').write('\n'.join(report))
    print('OK  题目 %d 道，可用 %d 道，异常 %d 道' %
          (len(all_questions), sum(1 for q in all_questions if q['valid']), len(bad)))
    print('    JSON %.2f MB' % (len(txt.encode('utf-8')) / 1024 / 1024))
    print('    报告：' + REPORT)

if __name__ == '__main__':
    main()
