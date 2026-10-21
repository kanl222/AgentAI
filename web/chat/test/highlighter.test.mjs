import { test } from 'node:test';
import assert from 'node:assert/strict';
import { highlightedCode } from '../public/highlighter.js';

test('SQL keywords, strings and numbers receive distinct syntax tokens',()=>{
  const result=highlightedCode("SELECT name FROM reports WHERE id = 42 AND kind = 'SSRS';",'sql');
  assert.equal(result.language,'sql');
  assert.match(result.html,/hljs-keyword/);assert.match(result.html,/hljs-string/);assert.match(result.html,/hljs-number/);
});
test('normalizes common aliases without guessing an unknown language',()=>{
  for(const [alias,expected] of [['js','javascript'],['TS','typescript'],['py','python'],['c#','csharp'],['ps1','powershell'],['postgresql','sql'],['language-json','json']]) {
    assert.equal(highlightedCode('const x = 1;',alias).language,expected);
  }
  for(const language of ['', 'unknown-language','__proto__','constructor','plaintext','text']) assert.equal(highlightedCode('SELECT 1;',language),null);
});
test('escapes HTML inside code instead of interpreting model markup',()=>{
  const result=highlightedCode('<img src=x onerror="alert(1)"><script>alert(1)</script>','html');
  assert(!result.html.includes('<img'));assert(!result.html.includes('<script'));
  assert(result.html.includes('&lt;'));
});
test('long code safely remains plain text',()=>{
  assert.equal(highlightedCode('x'.repeat(50_001),'javascript'),null);
});
