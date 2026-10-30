// prints "pitch\tsyllable" per note for verse 1, from abcjs's own parse
const abcjs=require('abcjs');
const src=require('fs').readFileSync(process.argv[2],'utf8');
const t=abcjs.parseOnly(src)[0];
const warn=(t.warnings||[]).length;
const out=[];
for(const l of t.lines) if(l.staff) for(const s of l.staff) for(const v of s.voices) for(const e of v)
  if(e.el_type==='note'&&e.pitches) out.push((e.lyric&&e.lyric[0]?e.lyric[0].syllable:'')+(e.lyric&&e.lyric[0]&&e.lyric[0].divider==='-'?'-':''));
console.log(JSON.stringify({warnings:warn, w:(t.warnings||[]).slice(0,5), notes:out.length, syl:out}));
