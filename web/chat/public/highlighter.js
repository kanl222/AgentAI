import hljs from './vendor/highlight/core.min.js';
import sql from './vendor/highlight/languages/sql.min.js';
import javascript from './vendor/highlight/languages/javascript.min.js';
import typescript from './vendor/highlight/languages/typescript.min.js';
import python from './vendor/highlight/languages/python.min.js';
import json from './vendor/highlight/languages/json.min.js';
import bash from './vendor/highlight/languages/bash.min.js';
import xml from './vendor/highlight/languages/xml.min.js';
import css from './vendor/highlight/languages/css.min.js';
import csharp from './vendor/highlight/languages/csharp.min.js';
import php from './vendor/highlight/languages/php.min.js';
import powershell from './vendor/highlight/languages/powershell.min.js';
import yaml from './vendor/highlight/languages/yaml.min.js';
import diff from './vendor/highlight/languages/diff.min.js';
import markdown from './vendor/highlight/languages/markdown.min.js';

for (const [name, grammar] of Object.entries({sql,javascript,typescript,python,json,bash,xml,css,csharp,php,powershell,yaml,diff,markdown})) hljs.registerLanguage(name,grammar);
const aliases={js:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',py:'python',sh:'bash',shell:'bash',console:'bash',html:'xml',cs:'csharp','c#':'csharp',ps1:'powershell',ps:'powershell',yml:'yaml',md:'markdown',postgresql:'sql',postgres:'sql',tsql:'sql',mysql:'sql'};

export function highlightedCode(source, fence) {
  const input=String(fence || '').trim().toLowerCase().replace(/^language-/, '').split(/\s+/)[0];
  const language=Object.hasOwn(aliases,input)?aliases[input]:input;
  // No speculative language detection or expensive parsing of huge responses.
  if (!hljs.getLanguage(language) || source.length>50_000) return null;
  try { return {language,html:hljs.highlight(source,{language,ignoreIllegals:true}).value}; }
  catch { return null; }
}

export function colorizeCode(element, source, fence) {
  element.textContent=source;
  const result=highlightedCode(source,fence);
  if (!result) return;
  const template=document.createElement('template');
  // Only trusted, escaping highlighter output is parsed. Model HTML is never parsed.
  template.innerHTML=result.html;
  const safe=document.createDocumentFragment();
  function copyTokens(node, parent) {
    for (const child of node.childNodes) {
      if (child.nodeType===Node.TEXT_NODE) parent.append(document.createTextNode(child.textContent));
      else if (child.nodeName==='SPAN') {
        const span=document.createElement('span');
        span.className=[...child.classList].filter(name=>/^(?:hljs-[\w-]+|[\w-]+_)$/.test(name)).join(' ');
        copyTokens(child,span);parent.append(span);
      } else parent.append(document.createTextNode(child.textContent || ''));
    }
  }
  copyTokens(template.content,safe);
  element.className='hljs language-'+result.language;
  element.replaceChildren(safe);
}
