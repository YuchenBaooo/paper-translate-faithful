/** Local PDF.js + Node canvas benchmark. Does not measure application scroll FPS. */
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {performance} from 'node:perf_hooks';
const args=process.argv.slice(2),file=args.shift(),output=args.shift();
let root,pages=[1,3,6,10],scale=2,runs=3,pngDir;
while(args.length){const key=args.shift(),value=args.shift();
  if(key==='--pdfjs-root')root=path.resolve(value);
  else if(key==='--pages')pages=value.split(',').map(Number);
  else if(key==='--scale')scale=Number(value);
  else if(key==='--runs')runs=Number(value);
  else if(key==='--png-dir')pngDir=path.resolve(value);
  else throw Error('Unknown option: '+key);
}
if(!file||!output||!root)throw Error('Usage: node pdfjs_benchmark.mjs INPUT.pdf OUTPUT.json --pdfjs-root DIR [--pages 1,3,6,10] [--scale 2] [--runs 3] [--png-dir DIR]');
if(!(scale>=1&&scale<=4&&runs>=1&&runs<=10&&Number.isInteger(runs))||pages.some(x=>!Number.isInteger(x)||x<1))throw Error('Invalid limits');
if(fs.existsSync(output))throw Error('Output exists');
const require=createRequire(path.join(root,'package.json'));
const {createCanvas,DOMMatrix,ImageData,Path2D}=require('@napi-rs/canvas');
Object.assign(globalThis,{DOMMatrix,ImageData,Path2D});
const lib=await import(pathToFileURL(path.join(root,'legacy/build/pdf.mjs')).href);
const folder=root.replaceAll('\\','/')+'/';
const trials=[];
for(let trial=0;trial<runs;trial++){
  const start=performance.now();
  const doc=await lib.getDocument({data:new Uint8Array(fs.readFileSync(file)),verbosity:0,standardFontDataUrl:folder+'standard_fonts/',cMapUrl:folder+'cmaps/',cMapPacked:true,wasmUrl:folder+'wasm/'}).promise;
  const row={open_ms:performance.now()-start,pages:doc.numPages,samples:[]};
  for(const n of pages.filter(x=>x<=doc.numPages)){
    const pg=await doc.getPage(n),t=performance.now(),operators=await pg.getOperatorList(),op=performance.now()-t;
    const viewport=pg.getViewport({scale}),canvas=createCanvas(Math.ceil(viewport.width),Math.ceil(viewport.height));
    const a=performance.now();await pg.render({canvasContext:canvas.getContext('2d'),viewport,canvas}).promise;const draw=performance.now()-a;
    const b=performance.now(),text=await pg.getTextContent(),extract=performance.now()-b;
    row.samples.push({page:n,operators:operators.fnArray.length,operator_list_ms:op,render_ms:draw,text_layer_ms:extract,text_chars:text.items.map(x=>x.str||'').join('').length});
    if(pngDir&&trial===0){fs.mkdirSync(pngDir,{recursive:true});const png=path.join(pngDir,n+'.png');if(fs.existsSync(png))throw Error('PNG exists: '+png);fs.writeFileSync(png,canvas.toBuffer('image/png'));}
    pg.cleanup();
  }
  await doc.destroy();trials.push(row);console.log(JSON.stringify({trial:trial+1,pages:row.samples.length}));
}
fs.writeFileSync(output,JSON.stringify({engine:'PDF.js '+lib.version,scope:'Local Node canvas; not in-app scrolling FPS',file:path.resolve(file),scale,trials},null,2),{flag:'wx'});
