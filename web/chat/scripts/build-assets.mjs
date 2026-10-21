import { mkdir, copyFile } from 'node:fs/promises';
import { codeLanguages } from '../public/highlight-languages.js';
const source=new URL('../node_modules/@highlightjs/cdn-assets/',import.meta.url);
const target=new URL('../public/vendor/highlight/',import.meta.url);
await mkdir(new URL('languages/',target),{recursive:true});
await copyFile(new URL('es/core.min.js',source),new URL('core.min.js',target));
for (const name of codeLanguages) await copyFile(new URL('es/languages/'+name+'.min.js',source),new URL('languages/'+name+'.min.js',target));
await copyFile(new URL('LICENSE',source),new URL('LICENSE',target));
