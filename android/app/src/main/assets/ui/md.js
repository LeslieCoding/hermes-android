// Small, dependency-free Markdown renderer for chat messages.
// Everything is HTML-escaped first; only a known set of constructs is turned into tags,
// and links are restricted to http(s)/mailto. Supports: fenced code, inline code,
// headings, emphasis, strikethrough, links/autolinks, lists (nested by indent),
// blockquotes, rules, and GFM pipe tables.
(function (global) {
  'use strict';

  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function safeUrl(url) {
    const u = url.trim().replace(/&amp;/g, '&');
    if (/^(https?:|mailto:)/i.test(u)) return esc(u);
    return null;
  }

  // Inline formatting on already-escaped text. Code spans are protected first.
  function inline(text) {
    const codes = [];
    let s = text.replace(/`([^`\n]+)`/g, function (_, code) {
      codes.push('<code>' + code + '</code>');
      return '\u0000' + (codes.length - 1) + '\u0000';
    });
    // links [text](url)
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, function (m, label, url) {
      const href = safeUrl(url);
      return href ? '<a href="' + href + '" target="_blank" rel="noopener">' + label + '</a>' : m;
    });
    // bare autolinks (not inside an href we just produced)
    s = s.replace(/(^|[^\w"'=\/>])(https?:\/\/[^\s<)）]+)/g, function (m, pre, url) {
      const trimmed = url.replace(/[.,;:!?。，；：！？]+$/, '');
      const rest = url.slice(trimmed.length);
      const href = safeUrl(trimmed);
      return href ? pre + '<a href="' + href + '" target="_blank" rel="noopener">' + trimmed + '</a>' + rest : m;
    });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>');
    s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return codes[+i]; });
    return s;
  }

  function isTableSep(line) {
    return /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line) && line.includes('-');
  }

  function splitRow(line) {
    let l = line.trim();
    if (l.startsWith('|')) l = l.slice(1);
    if (l.endsWith('|')) l = l.slice(0, -1);
    return l.split('|').map(function (c) { return c.trim(); });
  }

  function renderList(lines, start) {
    // Returns [html, nextIndex]. Handles nesting by indentation.
    const first = lines[start];
    const baseIndent = first.match(/^(\s*)/)[1].length;
    const ordered = /^\s*\d+[.)]\s/.test(first);
    let html = ordered ? '<ol>' : '<ul>';
    let i = start;
    while (i < lines.length) {
      const line = lines[i];
      const m = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (!m) break;
      const indent = m[1].length;
      if (indent < baseIndent) break;
      if (indent === baseIndent && /^\d/.test(m[2]) !== ordered) break;
      if (indent > baseIndent) {
        const sub = renderList(lines, i);
        html = html.replace(/<\/li>$/, '') + sub[0] + '</li>';
        i = sub[1];
        continue;
      }
      let body = m[3];
      const task = body.match(/^\[([ xX])\]\s+(.*)$/);
      if (task) body = (task[1] === ' ' ? '☐ ' : '☑ ') + task[2];
      html += '<li>' + inline(body) + '</li>';
      i++;
      // lazy continuation lines
      while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        html = html.replace(/<\/li>$/, '<br>' + inline(lines[i].trim()) + '</li>');
        i++;
      }
    }
    html += ordered ? '</ol>' : '</ul>';
    return [html, i];
  }

  function render(src) {
    if (!src) return '';
    const text = esc(String(src).replace(/\r\n?/g, '\n'));
    const lines = text.split('\n');
    const out = [];
    let para = [];
    const flush = function () {
      if (para.length) {
        out.push('<p>' + para.map(inline).join('<br>') + '</p>');
        para = [];
      }
    };
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*(```+|~~~+)\s*([\w+#.-]*)/);
      if (fence) {
        flush();
        const marker = fence[1];
        const lang = fence[2] || '';
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(marker)) {
          body.push(lines[i]);
          i++;
        }
        i++; // closing fence (or EOF while streaming)
        out.push('<div class="code"><div class="code-bar"><span>' + (lang || 'code') +
          '</span><button class="copy" type="button">复制</button></div><pre><code>' +
          body.join('\n') + '</code></pre></div>');
        continue;
      }
      if (/^\s*$/.test(line)) { flush(); i++; continue; }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { flush(); out.push('<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>'); i++; continue; }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push('<hr>'); i++; continue; }
      if (/^\s*&gt;/.test(line)) {
        flush();
        const quote = [];
        while (i < lines.length && /^\s*&gt;/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*&gt;\s?/, ''));
          i++;
        }
        out.push('<blockquote>' + render.fromEscaped(quote.join('\n')) + '</blockquote>');
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        flush();
        const res = renderList(lines, i);
        out.push(res[0]);
        i = res[1];
        continue;
      }
      if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        flush();
        const head = splitRow(line);
        i += 2;
        let t = '<div class="table"><table><thead><tr>' +
          head.map(function (c) { return '<th>' + inline(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
          t += '<tr>' + splitRow(lines[i]).map(function (c) { return '<td>' + inline(c) + '</td>'; }).join('') + '</tr>';
          i++;
        }
        out.push(t + '</tbody></table></div>');
        continue;
      }
      para.push(line);
      i++;
    }
    flush();
    return out.join('');
  }

  // Blockquotes recurse on text that is already escaped.
  render.fromEscaped = function (escaped) {
    const unescaped = escaped.replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&');
    return render(unescaped);
  };

  global.HermesMarkdown = { render: render, escape: esc };
})(window);
