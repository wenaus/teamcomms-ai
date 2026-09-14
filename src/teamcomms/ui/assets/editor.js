/* CodeMirror controls adapted from TJAI entry_detail.html, Apache-2.0. */
'use strict';
window.TeamCommsEditor = class {
  constructor(textarea, onChange, save, find, report) {
    this.cm = CodeMirror.fromTextArea(textarea, {
      mode: 'markdown', lineNumbers: true, lineWrapping: true,
      indentUnit: 2, tabSize: 2, indentWithTabs: false,
      extraKeys: {
        'Ctrl-S': save, 'Cmd-S': save, 'Ctrl-F': find, 'Cmd-F': find,
        'Ctrl-B': () => this.action('bold'), 'Cmd-B': () => this.action('bold'),
        'Ctrl-I': () => this.action('italic'), 'Cmd-I': () => this.action('italic'),
        'Ctrl-K': () => this.action('link'), 'Cmd-K': () => this.action('link'),
        Tab: cm => { if(!cm.getOption('readOnly')) return cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection('  ', 'end'); },
        'Shift-Tab': cm => { if(!cm.getOption('readOnly')) cm.indentSelection('subtract'); },
        Esc: cm => { cm.setOption('extraKeys', {...cm.getOption('extraKeys'), Tab: false}); },
        Enter: cm => {
          if(cm.getOption('readOnly'))return;
          const cur = cm.getCursor(), line = cm.getLine(cur.line);
          const m = line.match(/^(\s*)([-*+]|\d+[.)]) /);
          if (!m || cm.somethingSelected()) return cm.replaceSelection('\n');
          if (line.trim() === m[2]) return cm.replaceRange('', {line:cur.line,ch:0}, {line:cur.line,ch:line.length});
          if (cur.ch === 0) return cm.replaceSelection('\n');
          const bullet = /^\d/.test(m[2]) ? m[2].replace(/^\d+/, String(parseInt(m[2],10)+1)) : m[2];
          cm.replaceSelection('\n' + m[1] + bullet + ' ');
        },
      },
    });
    this.cm.getInputField().setAttribute('aria-label', 'Document source');
    this.cm.on('change', onChange);
    this.cm.on('cursorActivity', cm => {
      const cur = cm.getCursor();
      document.getElementById('position').textContent = `Line ${cur.line+1}, column ${cur.ch+1} · ${cm.getValue().length.toLocaleString()} / 40,000 characters`;
    });
    const turndown = new TurndownService({bulletListMarker:'-',headingStyle:'atx',codeBlockStyle:'fenced',emDelimiter:'*'});
    // HTML tables (including spans) retain their topology; the preview sanitizes HTML.
    turndown.keep(['table']);
    this.cm.on('paste', (cm,event) => {
      if(cm.getOption('readOnly'))return;
      const html = event.clipboardData?.getData('text/html');
      if (!html || !/<(ul|ol|table|a\s|strong|em|b>|i>|code|pre|h[1-6])\b/i.test(html)) return;
      try {
        const doc = new DOMParser().parseFromString(html,'text/html');
        doc.querySelectorAll('script,style,iframe,object,embed,meta,link').forEach(n=>n.remove());
        doc.querySelectorAll('*').forEach(n=>{
          for (const a of [...n.attributes]) if (/^on/i.test(a.name)) n.removeAttribute(a.name);
        });
        const converted = turndown.turndown(doc.body.innerHTML);
        if (converted) { event.preventDefault(); cm.replaceSelection(converted,'end','+input'); }
      } catch (error) { report('Rich paste failed; plain text was retained. ' + error.message); }
    });
  }
  get value() { return this.cm.getValue(); }
  set value(text) { this.cm.setValue(text); }
  action(name) {
    const cm = this.cm, selected = cm.getSelection();
    if(cm.getOption('readOnly'))return;
    cm.focus();
    if (name === 'undo' || name === 'redo') return cm[name]();
    const wrap = (before,after=before) => {
      cm.replaceSelection(before + selected + after,'around','+input');
    };
    if (name === 'bold') wrap('**');
    if (name === 'italic') wrap('*');
    if (name === 'heading') wrap('## ','');
    if (name === 'list') cm.replaceSelection(selected.split('\n').map(s=>'- '+s).join('\n'),'around','+input');
    if (name === 'code') wrap('\n```\n','\n```\n');
    if (name === 'table') cm.replaceSelection('\n| Column | Column |\n| --- | --- |\n|  |  |\n','around','+input');
    if (name === 'link') {
      const url = prompt('Link URL');
      if (url) cm.replaceSelection(`[${selected || 'Link'}](${url})`,'around','+input');
    }
    if (name === 'dedent') {
      const lines=selected.split('\n'), widths=lines.filter(s=>s.trim()).map(s=>s.match(/^ */)[0].length);
      const width=widths.length ? Math.min(...widths) : 0;
      cm.replaceSelection(lines.map(s=>s.slice(Math.min(width,s.match(/^ */)[0].length))).join('\n'),'around','+input');
    }
  }
  find(text) {
    if (!text) return false;
    const value=this.value, start=this.cm.indexFromPos(this.cm.getCursor('to'));
    let pos=value.indexOf(text,start);
    if (pos<0) pos=value.indexOf(text);
    if (pos<0) return false;
    this.cm.setSelection(this.cm.posFromIndex(pos),this.cm.posFromIndex(pos+text.length));
    this.cm.scrollIntoView(null,80);
    return true;
  }
};
