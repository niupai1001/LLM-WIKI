"""Version-bound reading layout; never rewrites the reviewed canonical text."""
import hashlib
import json
import re

VERSION = 'reading-layout-v1'
SYSTEM = '''你是文档排版编辑。仅返回JSON {documents:[{slug,sections:[{paragraph_index,title}]}]}。
输入为已审核正文的编号段落。只给连续论述分组补充简短中文章节标题，不能改写正文、添加事实或重新提炼。
每文用4到8个二级标题，按内容实际顺序组织，例如研究问题与动机、核心设计与机制、结果支持的认识、适用范围。
首个非标题段必须有章节标题，后续每个标题标明插入其前的paragraph_index整数。
不在既有标题段前插入标题、不重排段落；标题仅概括下方内容，避免新增结论或写作提示。'''


def digest(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def paragraphs(body):
    return [dict(paragraph_index=i, text=m.group(), start=m.start(), end=m.end())
            for i,m in enumerate(re.finditer(r'\S.*?(?=\n[ \t]*\n|\Z)',body,re.S))]


def has_sections(body):
    return bool(re.search(r'^#{2,6}[ \t]+(?!(?:原始证据|相关页面|参考文献)[ \t]*$)\S',body,re.M))


def format_body(body, plan):
    """Only insert headings. All original characters, including whitespace, survive."""
    if not isinstance(plan, dict) or set(plan)-{'sections'}:
        raise ValueError('Layout must contain only sections')
    if has_sections(body):
        if plan.get('sections'):
            raise ValueError('Existing section titles must not be duplicated')
        return body
    rows=paragraphs(body); sections=plan.get('sections')
    if not isinstance(sections,list) or not sections:
        raise ValueError('Missing reading section titles')
    before={}; last=-1
    for section in sections:
        if not isinstance(section,dict) or set(section)!={'paragraph_index','title'}:
            raise ValueError('Only heading labels and paragraph indices are allowed')
        i,title=section['paragraph_index'],section['title']
        if type(i) is not int or not 0<=i<len(rows) or i<=last:
            raise ValueError('Invalid, duplicate, or unordered paragraph index')
        if re.match(r'#{1,6}\s',rows[i]['text']):
            raise ValueError('Cannot insert a heading before an existing heading')
        if not isinstance(title,str) or not 2<=len(title.strip())<=40 or re.search(r'[\n\r<>\[\]#*`]',title):
            raise ValueError('Invalid plain heading label')
        before[rows[i]['start']]='## '+title.strip()+'\n\n'; last=i
    first=next((r for r in rows if not re.match(r'#{1,6}\s',r['text'])),None)
    if not first or first['start'] not in before:
        raise ValueError('First body paragraph needs a section title')
    output=body
    for index,heading in sorted(before.items(),reverse=True): output=output[:index]+heading+output[index:]
    return output


def record(library_id,slug,body,plan):
    formatted=format_body(body,plan)
    return {'version':VERSION,'library_id':library_id,'slug':slug,'body_sha256':digest(body),
            'plan':plan,'formatted_sha256':digest(formatted),'canonical_unchanged':True}


def display_body(ws,slug,body):
    """A stale or malformed presentation cannot replace the current reviewed page."""
    if ws is None: return body
    path=ws.root/'presentations'/(slug+'.json')
    try:
        value=json.loads(path.read_text())
        if (value.get('version')!=VERSION or value.get('library_id')!=ws.library_id or value.get('slug')!=slug
                or value.get('body_sha256')!=digest(body)): return body
        formatted=format_body(body,value['plan'])
        return formatted if value.get('formatted_sha256')==digest(formatted) else body
    except (OSError,ValueError,KeyError,TypeError): return body
