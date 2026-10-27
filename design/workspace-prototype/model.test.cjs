// These tests validate only the interactive prototype, not Flask or permissions.
const test=require('node:test');
const assert=require('node:assert/strict');
const M=require('./model.js');
test('empty project starts at stage 1 with no tasks or submissions',()=>{const m=M.create('empty');assert.equal(M.current(m),1);assert.equal(m.tasks.length,0);assert.ok(m.stages.every(s=>s.history.length===0));});
test('saving a draft does not submit or create a history version',()=>{const m=M.create('empty');M.save(m,1,'Тема и цель');assert.equal(m.stages[0].state,'progress');assert.equal(m.stages[0].history.length,0);});
test('all tasks done does not accept the stage',()=>{const m=M.create();m.tasks.forEach(t=>t.status='done');assert.equal(m.stages[2].state,'progress');assert.equal(M.current(m),3);});
test('missing supervisor blocks submission but allows a draft',()=>{const m=M.create('no-teacher');M.save(m,3,'Черновик');assert.throws(()=>M.submit(m,3,'Результат'),/руководителя/);assert.equal(m.stages[2].state,'progress');});
test('future and accepted stages cannot be edited',()=>{const m=M.create();assert.throws(()=>M.save(m,4,'Новая работа'));assert.throws(()=>M.save(m,1,'Перезапись'));assert.equal(M.canEdit(m,m.stages[2],'teacher'),false);});
test('review locks result editing',()=>{const m=M.create('review');assert.throws(()=>M.save(m,3,'Перезапись'));assert.equal(M.canEdit(m,m.stages[2],'student'),false);});
test('return requires feedback and preserves the submitted snapshot',()=>{const m=M.create('review'),text=m.stages[2].result;assert.throws(()=>M.decide(m,3,'return','  '));M.decide(m,3,'return','Добавьте вывод');M.save(m,3,'Новая редакция');assert.equal(m.stages[2].history[0].text,text);assert.equal(m.stages[2].history[1].text,text);});
test('resubmission increments version; old comment is not a new decision',()=>{const m=M.create();M.submit(m,3,'Исправлено');assert.equal(m.stages[2].version,2);assert.equal(M.label(m,m.stages[2]),'Повторная проверка');assert.match(M.next(m,m.stages[2],'teacher').title,/исправленный/);});
test('accepting opens stage 4 and keeps the accepted stage readable',()=>{const m=M.create('review');M.decide(m,3,'accept','Принято');assert.equal(M.current(m),4);assert.equal(m.stages[2].state,'done');assert.match(M.next(m,m.stages[2],'student').title,/сохранён/);});
test('fifth stage requires a presentation URL',()=>{const m=M.create('completed');m.stages[4].state='progress';assert.throws(()=>M.submit(m,5,'Описание',''),/HTTP/);M.submit(m,5,'Описание','https://example.org/slides');assert.equal(m.stages[4].state,'review');});
test('accepted stages do not mean accepted video defense',()=>{const m=M.create('completed');m.defense='draft';assert.equal(M.next(m,m.stages[4],'student').title,'Пять этапов приняты');assert.equal(M.current(m),null);});
test('next action does not invent the content of a new teacher comment',()=>{const m=M.create();m.stages[2].comment='Исправьте схему';assert.equal(M.next(m,m.stages[2],'student').title,'Доработайте результат этапа');});
test('accepted defense without published score is not shown as fully completed',()=>{const m=M.create('completed');m.published=false;assert.match(M.next(m,m.stages[4],'student').title,/ещё не опубликована/);});
