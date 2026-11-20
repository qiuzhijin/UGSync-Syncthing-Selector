"""Compile directory selections into ordered, rooted Syncthing patterns."""


def normalize(path):
    if not isinstance(path, str) or not path or path.startswith('/'):
        raise ValueError('需要相对目录路径')
    parts = path.split('/')
    if any(part in ('', '.', '..') or part != part.strip() or
           any(ord(c) < 32 for c in part) or '\\' in part for part in parts):
        raise ValueError('目录路径不受支持')
    return path


def literal(path, escape='\\'):
    # #escape makes these patterns portable between Windows and Linux.
    return ''.join(escape + c if c in '*?[]{}' + escape else c for c in path)


def _direct_file_parents(included, entire_root):
    if entire_root:
        return []
    parents = set()
    for name in included:
        parts = name.split('/')
        for depth in range(len(parts)):
            parent = '/'.join(parts[:depth])
            if parent not in included:
                parents.add(parent)
    return sorted(parents, key=lambda name: (-name.count('/'), name))


def direct_file_parents(included, excluded, entire_root):
    choices = {p: True for p in included} | {p: False for p in excluded}
    parents = []
    for parent in _direct_file_parents(included, False):
        nearest = max((p for p in choices if parent == p or parent.startswith(p + '/')),
                      key=len, default=None)
        if not (choices[nearest] if nearest else entire_root):
            parents.append(parent)
    return parents


DIRECT_FILES_MARKER = '// UGSync direct files'


def compile_selection(included=(), excluded=(), *, entire_root=False, escape='\\',
                      direct_files=None, _direct_files=True):
    if type(entire_root) is not bool:
        raise ValueError('根目录选择无效')
    includes = set(map(normalize, included))
    excludes = set(map(normalize, excluded))
    if includes & excludes:
        raise ValueError('目录不能同时包含和排除')
    rules = ['#escape=' + escape, '// Managed by UGSync Syncthing bridge']
    parents = set()
    if direct_files is not None:
        rules.append(DIRECT_FILES_MARKER)
        allowed_parents = set(direct_file_parents(includes, excludes, entire_root))
        for path in sorted(set(map(normalize, direct_files))):
            if path.rpartition('/')[0] not in allowed_parents or path in (includes | excludes):
                raise ValueError('直属文件不在已选范围内')
            exact = '/' + literal(path, escape)
            rules.extend([exact + '/**', '!' + exact])
        rules.append(DIRECT_FILES_MARKER)
    elif _direct_files is True:
        parents.update(_direct_file_parents(includes, False))
        if entire_root:
            parents = {p for p in parents if any(p == e or p.startswith(e + '/') for e in excludes)}
    # More-specific choices must precede inherited parent choices. Both the
    # directory and its descendants are explicit to avoid accidental globbing.
    for name in sorted(includes | excludes | parents, key=lambda p: (not p, -p.count('/'), p)):
        escaped = literal(name, escape)
        if name in parents:
            direct = '/' + (escaped + '/' if name else '') + '*'
            # Syncthing also matches the descendants of matching directories.
            # Deeper explicit choices already have precedence over this guard.
            rules.extend([direct + '/**', '!' + direct])
        if name in includes or name in excludes:
            prefix = '!' if name in includes else ''
            rules.extend([prefix + '/' + escaped, prefix + '/' + escaped + '/**'])
    if _direct_files == 'legacy':
        for parent in _direct_file_parents(includes, entire_root):
            rules.append('!/' + (literal(parent, escape) + '/' if parent else '') + '*')
    if not entire_root:
        rules.append('*')
    return rules


def parse_selection(rules, escape='\\'):
    """Only adopt our exact canonical format; keep foreign rules for review."""
    if not rules:
        return True, {}
    if rules[:2] != ['#escape=' + escape, '// Managed by UGSync Syncthing bridge']:
        return None
    entire = rules[-1] != '*'
    body = rules[2:] if entire else rules[2:-1]
    files = None
    if body and body[0] == DIRECT_FILES_MARKER:
        if body.count(DIRECT_FILES_MARKER) != 2:
            return None
        end = body.index(DIRECT_FILES_MARKER, 1)
        block, body = body[1:end], body[end + 1:]
        if len(block) % 2:
            return None
        import re
        files = []
        for guard, include in zip(block[::2], block[1::2]):
            if not include.startswith('!/') or guard != include[1:] + '/**':
                return None
            files.append(re.sub(re.escape(escape) + '(.)', r'\1', include[2:]))
    choices = {}
    index = 0
    while index < len(body):
        first = body[index]
        if (first.startswith('/') and first.endswith('/*/**') and index + 1 < len(body)
                and body[index + 1] == '!' + first[:-3]):
            index += 2
            continue
        if first.startswith('!/') and first.endswith('/*'):
            index += 1
            continue
        if index + 1 == len(body):
            return None
        second = body[index + 1]
        included = first.startswith('!/')
        if not first.startswith('!/' if included else '/') or second != first + '/**':
            return None
        escaped = first[2:] if included else first[1:]
        name, character_index = '', 0
        while character_index < len(escaped):
            if escaped[character_index] == escape:
                character_index += 1
                if character_index == len(escaped):
                    return None
            name += escaped[character_index]
            character_index += 1
        try:
            choices[normalize(name)] = included
        except ValueError:
            return None
        index += 2
    includes = [p for p, selected in choices.items() if selected]
    excludes = [p for p, selected in choices.items() if not selected]
    if files is not None:
        try:
            canonical = compile_selection(includes, excludes, entire_root=entire,
                                          escape=escape, direct_files=files)
        except ValueError:
            return None
        return (entire, choices) if rules == canonical else None
    canonical = compile_selection(includes, excludes, entire_root=entire, escape=escape)
    legacy = compile_selection(includes, excludes, entire_root=entire, escape=escape, _direct_files=False)
    older_helpers = compile_selection(includes, excludes, entire_root=entire, escape=escape, _direct_files='legacy')
    return (entire, choices) if rules in (canonical, legacy, older_helpers) else None


DOCUMENT_MARKER = '// UGSync selection begins (v2)'


def parse_document(rules, *, native_escape='|'):
    """Preserve exclusion-only preambles without changing their escape mode."""
    import re
    restored = parse_selection(rules)
    if restored is not None:
        return [], *restored, '\\'
    if rules.count(DOCUMENT_MARKER) > 1:
        raise ValueError('选择规则标记重复，请在 Syncthing 中核对')
    split = rules.index(DOCUMENT_MARKER) if DOCUMENT_MARKER in rules else len(rules)
    base = list(rules[:split])
    escape = native_escape
    for line in base:
        stripped = line.strip()
        if stripped.startswith('#escape='):
            escape = stripped[len('#escape='):]
            if len(escape) != 1:
                raise ValueError('规则转义字符不受支持')
        elif stripped.startswith('#include') or re.sub(r'^(?:\(\?[id]+\))*', '', stripped).startswith('!'):
            raise ValueError('现有规则含包含指令，请先在 Syncthing 中管理，避免改变其含义')
    if split < len(rules):
        restored = parse_selection(['#escape=' + escape] + rules[split + 1:], escape)
        if restored is None:
            raise ValueError('保存的目录选择格式不受支持')
        return base, *restored, escape
    entire = not (base and base[-1] == '*')
    if not entire:
        base.pop()
    return base, entire, {}, escape


def compile_document(base, included=(), excluded=(), *, entire_root=False, escape='|', direct_files=None):
    selected = compile_selection(included, excluded, entire_root=entire_root, escape=escape,
                                 direct_files=direct_files)
    preserved = list(base)
    if not any(line.strip().startswith('#escape=') for line in preserved):
        preserved.insert(0, '#escape=' + escape)
    return preserved + [DOCUMENT_MARKER] + selected[1:]
