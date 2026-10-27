/* Standalone interaction study. All writes modify synthetic memory only. */
(() => {
  'use strict';
  const M=WorkspaceModel, $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=n=>`<span class="ico" data-ui-icon="${n}" aria-hidden="true"></span>`;
  const btn=(a,t,cl='quiet',i='')=>`<button class="${cl}" data-action="${a}">${i?icon(i):''}${t}</button>`;
  const options=(values,selected)=>Object.entries(values).map(([k,v])=>`<option value="${esc(k)}" ${String(k)===String(selected??'')?'selected':''}>${esc(v)}</option>`).join('');
  const dialog=$('#dialog');let trigger=null,toastTimer,activeTask=null,returnFocus=null;
  let model=M.create(), role='student', selected=3, mine=false, filters={q:'',priority:'',person:'',deadline:'',sort:'priority'}, filtersOpen=false, doneOpen=false, unassignedOpen=false;
  function setAccount(open){const account=$('.account');account.open=open;account.querySelector('summary').setAttribute('aria-expanded',String(open));}
  function announce(text){clearTimeout(toastTimer);$('#announcement').textContent=text;toastTimer=setTimeout(()=>$('#announcement').textContent='',5000);}
  function writeUrl(){const p=new URLSearchParams({role,example:$('#demo-state').value,stage:selected,theme:$('#demo-theme').value});history.replaceState(null,'','#'+p);}
  function resetFilters(){mine=false;filters={q:'',priority:'',person:'',deadline:'',sort:'priority'};doneOpen=false;}
  function loadExample(){model=M.create($('#demo-state').value);selected=M.current(model)||5;resetFilters();filtersOpen=false;unassignedOpen=false;close();render();announce('Пример загружен. Все данные демонстрационные.');}
  function shell(){
    const teacher=role==='teacher';
    const nav=teacher?[['home','Кабинет','outside'],['folder','Проекты учеников','outside'],['groups','Учебные группы','groups'],['review','Проверка работ','outside'],['assessment','Оценивание','finish']]:[['home','Главная','outside'],['folder','Мои проекты','outside'],['groups','Учебные группы','groups'],['team','Команды и люди','team'],['results','Мои результаты','finish']];
    const links=nav.map(([i,t,a])=>btn(a,t,i==='folder'?'current':'',i)).join('');
    $('#sidebar').innerHTML=`<a class="brand" href="#work">M-Flow<span>.</span></a><p class="eyebrow">ПРОСТРАНСТВО ${teacher?'УЧИТЕЛЯ':'УЧЕНИКА'}</p><nav class="side-nav" aria-label="Основные разделы">${links}</nav><div class="side-bottom"><p class="side-motto">От первой идеи<br>до своего результата.<small>Это твой M-Flow.</small></p><div class="side-nav">${btn('profile','Мой профиль','','profile')}${btn('settings','Настройки','','settings')}</div></div>`;
    $('#account-panel').innerHTML=`<div><strong>${teacher?'Мария Сергеевна':'Алексей'}</strong><small>${teacher?'Учитель':'Ученик · владелец проекта'}</small></div>${btn('profile','Мой профиль','','profile')}${btn('settings','Настройки','','settings')}<hr>${btn('logout','Выйти','','logout')}`;
    $('#teacher-name').textContent=model.hasTeacher?'Мария Сергеевна':'Не подключён';
    $('.crumb').innerHTML=`${teacher?'Проекты учеников':'Мои проекты'} <span>/</span> Умная теплица`;
  }
  function stageKind(s){return s.state==='done'?'accepted':s.state==='review'?'review':s.reviewed?'returned':s.state;}
  function render(){
    shell();writeUrl();
    const s=model.stages[selected-1], cur=M.current(model), next=M.next(model,s,role), locked=cur && selected>cur;
    $('#route-count').textContent=`${model.stages.filter(x=>x.state==='done').length} из 5 принято учителем`;
    $('#stage-path').innerHTML=model.stages.map(x=>`<button class="stage-button ${esc(stageKind(x))} ${x.state==='done'?'done':''} ${cur&&x.number>cur?'locked':''}" data-stage="${x.number}" ${selected===x.number?'aria-current="step"':''} aria-label="Этап ${x.number}. ${esc(x.name)}. ${M.label(model,x)}"><span class="stage-top"><span class="stage-number">${x.state==='done'?icon('check'):x.number.toString().padStart(2,'0')}</span><strong>${esc(x.short)}</strong></span><span class="stage-status">${esc(M.label(model,x))}</span></button>`).join('');
    $('#workspace').innerHTML=`<div class="stage-heading"><div><span class="eyebrow">ЭТАП ${selected} / 5 ${selected===cur?'· ТЕКУЩИЙ':''}</span><h2>${esc(s.name)}</h2><p>${esc(s.goal)}</p></div>${selected!==cur&&cur?btn('current','К текущему этапу','quiet back-current','arrow'):''}</div>
      <section class="next-step ${next.kind}" aria-label="Следующий шаг"><div><p class="eyebrow">${locked?'ВПЕРЕДИ':s.state==='done'?'РЕЗУЛЬТАТ':role==='teacher'?'ВАШ СЛЕДУЮЩИЙ ШАГ':'ЧТО ДЕЛАТЬ СЕЙЧАС'}</p><h3>${esc(next.title)}</h3><p>${esc(next.text)}</p></div>${btn(next.action,next.button,'primary','arrow')}</section>
      <div class="work-grid"><section class="tasks-section" aria-labelledby="tasks-title"><div class="section-head"><div><h3 id="tasks-title">Задачи этапа</h3><small>${role==='teacher'?'Наблюдайте за работой команды':'Дела команды на этом этапе'}</small></div>${role==='student'?btn('add-task','Задача','quiet','plus'):''}</div>
        <div class="task-tools">${role==='student'?`<div class="segmented" aria-label="Чьи задачи"><button data-scope="all" aria-pressed="${!mine}">Вся команда</button><button data-scope="mine" aria-pressed="${mine}">Мои</button></div>`:'<span class="muted"><small>Вся команда · только просмотр</small></span>'}<details class="filter-box" ${filtersOpen?'open':''}><summary aria-controls="task-filters" aria-expanded="${filtersOpen}">${icon('filter')}Фильтры</summary></details></div>
        <div class="filters" id="task-filters" ${filtersOpen?'':'hidden'}><label>Название<input type="search" id="filter-q" data-filter="q" value="${esc(filters.q)}" placeholder="Найти задачу"></label><label>Приоритет<select data-filter="priority">${options({'':'Любой',...M.priorities},filters.priority)}</select></label><label>Исполнитель<select data-filter="person">${options({'':'Любой',alex:'Алексей',dasha:'Дарья',max:'Максим',none:'Не назначен'},filters.person)}</select></label><label>Срок<select data-filter="deadline">${options({'':'Любой',soon:'Ближайшие 7 дней',overdue:'Просрочены',none:'Без срока'},filters.deadline)}</select></label><label>Сортировка<select data-filter="sort">${options({priority:'По приоритету',deadline:'По сроку',title:'По названию'},filters.sort)}</select></label>${btn('reset-filters','Сбросить фильтры','quiet filter-reset')}</div>
        <div id="tasks-content"></div></section>
        <section class="result-section" aria-label="Результат и обратная связь"><div class="result-card" id="result-card" tabindex="-1">${resultHtml(s,locked)}</div></section></div>
      <section class="finish-strip"><div><h3>Видеозащита и итоговая оценка</h3><p>${model.defense==='accepted'?'Защита принята · оценки опубликованы':'Отдельный шаг завершения. Материалы доступны на любом этапе.'}</p></div>${btn('finish','Открыть','quiet','arrow')}</section>`;
    renderTasks();
  }
  function resultHtml(s,locked){
    const editable=M.canEdit(model,s,role), teacher=role==='teacher', previous=s.state==='review'&&s.comment;
    return `<h3>${s.number===5?'Описание проекта':'Результат этапа'}</h3><div class="result-state"><span class="badge ${stageKind(s)}">${esc(M.label(model,s))}</span>${s.history.length?` <span class="muted"><small>Версия ${s.version}</small></span>`:''}</div>
      ${s.result?`<p class="result-body">${esc(s.result.replace(/\n\nЖурнал испытаний: https:\/\/example\.org\/greenhouse-log/,''))}</p><p class="result-caption">${s.state==='review'?'Отправил Алексей · новая версия на проверке':s.state==='done'?'Приняла Мария Сергеевна':s.reviewed?'Возвращённая работа · новая версия ещё не отправлена':'Черновик команды · ещё не отправлен'}</p>`:`<p class="muted result-body">${locked?'Сдача результата откроется после принятия предыдущего этапа.':teacher?'Команда пока не добавила результат.':'Здесь будет то, что вы сделали: описание, выводы и ссылки на материалы.'}</p>`}
      ${s.comment?`<div class="feedback ${previous?'previous':''}"><strong>${previous?'Замечание к предыдущей версии':s.state==='done'?'Решение учителя':'Что нужно исправить'}</strong><p>${esc(s.comment)}</p><small>Мария Сергеевна${previous?' · новая версия ещё не проверена':''}</small></div>`:''}
      ${s.presentation||s.result.includes('https://example.org/greenhouse-log')?`<div class="materials"><h4>Материалы результата</h4>${btn('material',s.number===5?'Презентация «Умная теплица»':'Журнал испытаний · ссылка из результата','link-button','link')}</div>`:''}
      ${editable?`<div class="result-actions">${btn('edit-result',s.result?'Редактировать черновик':'Добавить результат','','edit')}</div>${!model.hasTeacher?'<p class="waiting-note">Для отправки нужен подтверждённый руководитель. Черновик доступен.</p>':''}`:''}
      ${s.state==='review'&&teacher?`<form class="review-form" id="review-form"><label class="field">Комментарий учителя<textarea name="comment" maxlength="2000" rows="3" placeholder="Что получилось и что нужно улучшить"></textarea></label><p class="error-text" id="review-error" role="alert"></p><div class="result-actions"><button type="submit" name="decision" value="accept" class="primary">${icon('check')}${s.number===1?'Согласовать тему':'Принять этап'}</button><button type="submit" name="decision" value="return">${icon('return')}Вернуть на доработку</button></div></form>`:''}
      ${s.state==='review'&&!teacher?'<p class="waiting-note">Результат на проверке. Редактирование откроется, если учитель вернёт его на доработку.</p>':''}
      ${teacher&&s.state!=='review'&&s.state!=='done'&&!locked?'<p class="waiting-note">Решение можно сохранить после отправки результата учениками.</p>':''}
      ${s.history.length?btn('stage-history','История отправок и решений','link-button','history'):''}`;
  }
  function filtered(tasks){
    let list=tasks.filter(t=>(!mine||t.assignee==='alex')&&(!filters.q||t.title.toLocaleLowerCase('ru').includes(filters.q.toLocaleLowerCase('ru')))&&(!filters.priority||t.priority===filters.priority)&&(!filters.person||t.assignee===(filters.person==='none'?'':filters.person))&&(!filters.deadline||filters.deadline==='none'&&!t.deadline||filters.deadline==='overdue'&&t.deadline&&t.deadline<'2026-10-26'&&t.status!=='done'||filters.deadline==='soon'&&t.deadline>='2026-10-26'&&t.deadline<='2026-11-02'&&t.status!=='done'));
    const rank={urgent:0,high:1,normal:2,low:3};
    return list.sort((a,b)=>filters.sort==='title'?a.title.localeCompare(b.title,'ru'):filters.sort==='deadline'?(a.deadline||'9999').localeCompare(b.deadline||'9999'):rank[a.priority]-rank[b.priority]||a.id-b.id);
  }
  function deadline(t){if(!t.deadline)return 'Без срока';const d=new Date(t.deadline+'T12:00:00');return d.toLocaleDateString('ru-RU',{day:'numeric',month:'short'}).replace(' г.','');}
  function taskRow(t){return `<article class="task-row"><span class="task-marker ${t.status}" aria-hidden="true">${t.status==='done'?icon('check'):''}</span><div><button class="task-name" data-task="${t.id}">${esc(t.title)}</button><div class="task-meta"><span>${esc(role==='teacher'&&t.assignee==='alex'?'Алексей':M.people[t.assignee])}</span><span class="${t.deadline&&t.deadline<'2026-10-26'&&t.status!=='done'?'overdue':''}">${esc(deadline(t))}</span>${['high','urgent'].includes(t.priority)?`<span class="badge high">${M.priorities[t.priority]}</span>`:''}${t.reports.length?`<span>${t.reports.length} отчёт</span>`:''}</div></div>${role==='student'?`<select data-task-status="${t.id}" aria-label="Состояние задачи: ${esc(t.title)}">${options(M.statuses,t.status)}</select>`:`<span class="badge ${t.status==='done'?'accepted':t.status}">${M.statuses[t.status]}</span>`}</article>`;}
  function renderTasks(){
    const all=model.tasks.filter(t=>t.stage===selected), visible=filtered(all), active=visible.filter(t=>t.status!=='done'), done=visible.filter(t=>t.status==='done'), unassigned=filtered(model.tasks.filter(t=>t.stage===null));
    $('#tasks-content').innerHTML=`${all.length?`<p class="filter-count">Показано ${visible.length} из ${all.length} · выполнено ${all.filter(t=>t.status==='done').length} из ${all.length}</p>`:''}
      ${active.length?`<div class="task-list">${active.map(taskRow).join('')}</div>`:`<div class="empty">${icon(all.length?'check':'tasks')}<h3>${!all.length?'Начните с небольшого дела':done.length?'Все показанные задачи выполнены':'Нет задач по этим условиям'}</h3><p>${!all.length?'Добавьте задачи и распределите их между участниками.':done.length?'Завершение задач не отправляет и не принимает результат этапа.':'Попробуйте изменить условия или показать всю команду.'}</p>${!all.length&&role==='student'?btn('add-task','Добавить задачу','','plus'):!visible.length&&all.length?btn('reset-filters','Сбросить фильтры','link-button'):''}</div>`}
      ${done.length?`<details class="done-list" ${doneOpen?'open':''}><summary>${icon('chevron')}Выполненные · ${done.length}</summary><div class="task-list">${done.map(taskRow).join('')}</div></details>`:''}
      <details class="unassigned" ${unassignedOpen?'open':''}><summary>${icon('chevron')}Без этапа · ${unassigned.length}</summary><p class="muted"><small>Общие дела проекта. Владелец может привязать их к этапу.</small></p>${unassigned.length?`<div class="task-list">${unassigned.map(taskRow).join('')}</div>`:'<p class="muted"><small>Задач без этапа по этим условиям нет.</small></p>'}${role==='student'?btn('add-unassigned','Добавить общее дело','link-button','plus'):''}</details>`;
  }
  function open(title,body){
    if(!dialog.open){trigger=document.activeElement;dialog.showModal();document.body.style.overflow='hidden';}
    $('#dialog-title').textContent=title;$('#dialog-content').innerHTML=body;dialog.scrollTop=0;
    requestAnimationFrame(()=>dialog.querySelector('input:not([type=hidden]),textarea,select')?.focus());
  }
  function close(target=null){returnFocus=target;if(dialog.open)dialog.close();}
  dialog.addEventListener('close',()=>{document.body.style.overflow='';$('.mobile-menu').setAttribute('aria-expanded','false');if(returnFocus&&$(returnFocus))$(returnFocus).focus();else if(trigger?.isConnected)trigger.focus();else $('#result-card')?.focus();returnFocus=null;});
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});
  function taskForm(task=null,unassigned=false){
    if(role!=='student')return;
    const t=task||{title:'',description:'',deadline:'',assignee:'alex',priority:'normal',stage:unassigned?null:selected,status:'todo'};
    open(task?'Редактировать задачу':'Новая задача',`<form id="task-form" data-edit="${task?.id||''}"><label class="field">Название<input name="title" required maxlength="160" value="${esc(t.title)}" placeholder="Что нужно сделать?"></label><label class="field">Описание<textarea name="description" maxlength="3000">${esc(t.description)}</textarea></label><div class="fields-pair"><label class="field">Исполнитель<select name="assignee">${options(M.people,t.assignee)}</select></label><label class="field">Срок<input type="date" name="deadline" value="${esc(t.deadline)}"></label><label class="field">Приоритет<select name="priority">${options(M.priorities,t.priority)}</select></label><label class="field">Этап<select name="stage">${options({'':'Без этапа',...Object.fromEntries(model.stages.map(s=>[s.number,`${s.number}. ${s.short}`]))},t.stage)}</select></label></div><div class="actions"><button type="submit" class="primary">${icon('save')}${task?'Сохранить':'Добавить задачу'}</button>${btn('close','Отмена','')}</div></form>`);
  }
  function showTask(id){
    const t=model.tasks.find(x=>x.id===id);if(!t)return;activeTask=id;
    open(t.title,`<div class="task-meta"><span class="badge ${t.status==='done'?'accepted':t.status}">${M.statuses[t.status]}</span><span>${t.stage?'Этап '+t.stage:'Без этапа'}</span></div><p>${esc(t.description||'Описание пока не добавлено.')}</p><div class="person-row"><span class="person-initial">${icon('profile')}</span><div>${esc(M.people[t.assignee])}<small>${deadline(t)} · ${M.priorities[t.priority]} приоритет</small></div></div>${role==='student'?`<div class="actions">${btn('edit-task','Редактировать','','edit')}${!t.reports.length?btn('delete-task','Удалить','danger','trash'):''}</div>`:''}<h3>Отчёты по задаче</h3>${t.reports.length?t.reports.map(r=>`<article class="history-entry"><strong>${esc(r.who)}</strong><small>${esc(r.date)}</small><p>${esc(r.text)}</p></article>`).join(''):'<p class="muted">Отчётов пока нет.</p>'}${role==='student'&&t.assignee==='alex'?'<form id="report-form"><label class="field">Новый отчёт<textarea name="text" required maxlength="3000" placeholder="Что сделано и какой результат получен?"></textarea></label><button type="submit" class="primary">Добавить отчёт</button></form>':role==='student'?'<p class="demo-notice">Отчёт добавляет назначенный исполнитель. Сейчас вы — Алексей.</p>':'<p class="demo-notice">Учитель просматривает задачи и отчёты. Решение сохраняется для результата этапа.</p>'}`);
  }
  function editResult(){
    const s=model.stages[selected-1];if(!M.canEdit(model,s,role))return;
    open(s.reviewed?'Доработка результата':'Результат этапа '+selected,`${s.comment?`<div class="feedback"><strong>Замечание учителя</strong><p>${esc(s.comment)}</p></div>`:''}<form id="result-form"><label class="field">${selected===5?'Описание проекта':'Что сделано и какие выводы получены'}<textarea name="result" rows="10" maxlength="10000">${esc(s.result)}</textarea></label>${selected===5?`<label class="field">Ссылка на презентацию<input type="url" name="presentation" value="${esc(s.presentation)}" placeholder="https://…"></label>`:''}<p class="muted"><small>Ссылки на материалы можно добавить в текст результата. При отправке сохраняется отдельная версия.</small></p><p id="result-error" class="error-text" role="alert"></p><div class="actions"><button type="submit" name="action" value="save">${icon('save')}Сохранить черновик</button><button type="submit" name="action" value="submit" class="primary" ${model.hasTeacher?'':'disabled'}>${icon('send')}${s.reviewed?'Отправить повторно':'Отправить учителю'}</button></div>${!model.hasTeacher?'<p class="demo-notice">Отправка станет доступна после подтверждения руководителя.</p>':''}</form>`);
  }
  function historyView(stage=null){
    const entries=model.stages.filter(s=>!stage||s.number===stage).flatMap(s=>s.history.map((h,i)=>({...h,stage:s.number,index:i}))).reverse();
    open(stage?'История этапа '+stage:'История работ и решений',`<p class="demo-notice">Отправленные версии и решения сохранены отдельно. Черновики в историю не попадают.</p>${entries.length?entries.map(h=>`<article class="history-entry"><span class="badge ${h.type==='accepted'?'accepted':h.type==='returned'?'returned':'review'}">${{accepted:'Принято',returned:'Возвращено',submitted:'Отправлено'}[h.type]}</span><p><strong>Этап ${h.stage} · версия ${h.version}</strong></p><small>${esc(h.who)} · ${esc(h.date)}</small>${h.comment?`<p>${esc(h.comment)}</p>`:''}<details><summary>Результат этой версии</summary><p class="result-body">${esc(h.text)}</p></details></article>`).join(''):'<p class="muted">Пока нет отправок. Здесь появится первая версия после сдачи результата.</p>'}`);
  }
  function panel(name){
    if(name==='team')open('Команда проекта',`<p class="muted">У каждого дела есть свой исполнитель.</p>${[['А','Алексей','Владелец · программирование'],['Д','Дарья','Анализ данных'],['М','Максим','Сборка и испытания']].map(([a,b,c])=>`<div class="person-row"><span class="person-initial">${a}</span><div>${b}<small>${c}</small></div></div>`).join('')}<p class="demo-notice">Управление участниками и приглашениями останется в существующем разделе команды. В этом макете показано его расположение.</p>`);
    else if(name==='finish')open('Защита и результат',`<p class="demo-notice">Этот раздел доступен независимо от выбранного этапа. Принятие этапов, видеозащита и личные оценки — отдельные решения.</p><h3>Видеозащита</h3><span class="badge ${model.defense==='accepted'?'accepted':''}">${model.defense==='accepted'?'Принята учителем':'Черновик · ещё не отправлена'}</span><p>${model.defense==='accepted'?'Запись выступления сохранена вместе с решением учителя.':'Здесь останется существующая форма ссылки и описания записи.'}</p><h3>${role==='teacher'?'Оценивание участников':'Моя оценка'}</h3><p>${model.published?role==='teacher'?'Алексей — 41 / 45 · опубликовано<br>Дарья — 42 / 45 · опубликовано<br>Максим — 40 / 45 · опубликовано':'41 / 45 · опубликовано Марией Сергеевной':'Оценка пока не опубликована.'}</p><p class="muted"><small>Существующие критерии и доступ к личным оценкам сохраняются.</small></p>`);
    else if(name==='supervisor')open('Руководитель проекта',`<h3>${model.hasTeacher?'Мария Сергеевна':'Руководитель пока не подключён'}</h3><p>${model.hasTeacher?'Сопровождение подтверждено обеими сторонами.':'Владелец принимает приглашение для этого проекта, затем учитель подтверждает сопровождение.'}</p><p class="demo-notice">Здесь останется переход к существующему согласованию руководителя. Макет не отправляет приглашения и не меняет сопровождение.</p>`);
    else if(name==='overview')open('Обзор проекта',`<h3>Умная теплица</h3><p>Создаём систему автоматического полива для школьного сада и проверяем её на реальных измерениях.</p><div class="person-row">${icon('groups')}<div>10 «А»<small>Учебная группа</small></div></div><div class="person-row">${icon('profile')}<div>${model.hasTeacher?'Мария Сергеевна':'Не подключён'}<small>Руководитель проекта</small></div></div><p class="demo-notice">Общие сведения остаются в обзоре. Задачи, результаты и следующие шаги собраны в «Работе над проектом».</p>`);
    else if(name==='groups')open('Учебные группы','<h3>10 «А»</h3><p>Группа проекта · Мария Сергеевна</p><p class="demo-notice">Учебные группы остаются отдельным существующим разделом. Здесь показан переход, без изменения групп и приглашений.</p>');
    else if(name==='material')open('Материалы результата',`<p class="demo-notice">Пример перехода по ссылке из результата. В приложении откроется исходный документ во внешнем сервисе; новая загрузка файлов не добавляется.</p><h3>${selected===5?'Презентация «Умная теплица»':'Журнал испытаний'}</h3><p>${selected===5?'Цель, схема, результаты испытаний и выводы команды.':'Влажность утром и вечером, время включения насоса, наблюдения команды.'}</p>`);
    else if(name==='menu'){open('Меню',`<nav class="mobile-nav-list" aria-label="Мобильная навигация">${$('#sidebar .side-nav').innerHTML}${btn('settings','Настройки','','settings')}</nav>`);$('.mobile-menu').setAttribute('aria-expanded','true');}
    else if(name==='notifications')open('Уведомления',`<p>${model.scenario==='returned'?'Мария Сергеевна вернула этап 3 на доработку.':'Новых уведомлений в этом примере нет.'}</p>${btn('go-feedback','К работе над проектом','','arrow')}`);
    else if(name==='settings')open('Настройки','<label class="field">Тема сайта<select id="panel-theme"><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label><p class="demo-notice">В макете тема также переключается сверху. Остальные настройки приложения сохраняются.</p>');
    else if(name==='profile')open('Мой профиль',`<h3>${role==='teacher'?'Мария Сергеевна':'Алексей'}</h3><p>${role==='teacher'?'Учитель':'Ученик · 10 «А»'}</p><p class="demo-notice">Профиль сохраняется как отдельная страница. Его редактирование не входит в этот макет.</p>`);
    else if(name==='logout')open('Выход из аккаунта','<p class="demo-notice">Это автономный макет без авторизации. В приложении выход останется POST-формой с CSRF-защитой.</p>');
    else open('Раздел M-Flow','<p class="demo-notice">Этот раздел сохраняется в приложении. Здесь проверяем новое пространство проекта; остальные страницы не воспроизводятся.</p>');
    if(name==='settings')$('#panel-theme').value=$('#demo-theme').value;
  }
  function handleAction(action){
    if(action==='close')return close();
    if(action==='current'){selected=M.current(model)||5;resetFilters();render();return;}
    if(action==='result'||action==='tasks'){const target=$(action==='result'?'#result-card':'#tasks-title');target.scrollIntoView({block:'center',behavior:'auto'});if(action==='result')target.focus();else $('[data-task]')?.focus();return;}
    if(action==='edit-result')return editResult();
    if(action==='add-task'||action==='add-unassigned')return taskForm(null,action==='add-unassigned');
    if(action==='edit-task')return taskForm(model.tasks.find(t=>t.id===activeTask));
    if(action==='delete-task'){const t=model.tasks.find(t=>t.id===activeTask);if(role!=='student'||!t||t.reports.length)return;return open('Удалить задачу?',`<p>${esc(t.title)}</p><p class="muted">У этой задачи нет отчётов. Изменится только пример в этом окне.</p><div class="actions">${btn('confirm-delete','Удалить','danger','trash')}${btn('close','Отмена','')}</div>`);}
    if(action==='confirm-delete'){if(role!=='student')return;model.tasks=model.tasks.filter(t=>t.id!==activeTask||t.reports.length);close();render();announce('Задача удалена из примера.');return;}
    if(action==='reset-filters'){resetFilters();render();return;}
    if(action==='history'||action==='stage-history')return historyView(action==='stage-history'?selected:null);
    if(action==='go-feedback'){close();selected=M.current(model)||5;render();$('#result-card').scrollIntoView({block:'center'});$('#result-card').focus();return;}
    panel(action);
  }
  document.addEventListener('click',e=>{
    if(e.target.closest('.account>summary')){e.preventDefault();setAccount(!$('.account').open);return;}
    const stage=e.target.closest('[data-stage]');if(stage){selected=Number(stage.dataset.stage);resetFilters();render();$(`[data-stage="${selected}"]`).focus();return;}
    const scope=e.target.closest('[data-scope]');if(scope){mine=scope.dataset.scope==='mine';render();$(`[data-scope="${mine?'mine':'all'}"]`).focus();return;}
    const task=e.target.closest('[data-task]');if(task)return showTask(Number(task.dataset.task));
    const a=e.target.closest('[data-action]');if(a){setAccount(false);handleAction(a.dataset.action);}
    if(!e.target.closest('.account'))setAccount(false);
    const work=e.target.closest('a[href="#work"]');if(work){e.preventDefault();selected=M.current(model)||5;resetFilters();render();$('#work').scrollIntoView({block:'start'});}
  });
  document.addEventListener('keydown',e=>{
    if(dialog.open&&e.key==='Tab'){
      const items=[...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,a[href]')].filter(x=>x.getClientRects().length);
      const first=items[0],last=items.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
    if(e.key==='Escape'&&$('.account').open){setAccount(false);$('.account>summary').focus();}
  });
  document.addEventListener('toggle',e=>{
    if(e.target.matches('.filter-box')){filtersOpen=e.target.open;$('#task-filters').hidden=!filtersOpen;e.target.querySelector('summary').setAttribute('aria-expanded',String(filtersOpen));}
    if(e.target.matches('.account'))e.target.querySelector('summary').setAttribute('aria-expanded',String(e.target.open));
    if(e.target.matches('.done-list'))doneOpen=e.target.open;
    if(e.target.matches('.unassigned'))unassignedOpen=e.target.open;
  },true);
  document.addEventListener('input',e=>{if(e.target.matches('[data-filter]')){filters[e.target.dataset.filter]=e.target.value;renderTasks();}});
  document.addEventListener('change',e=>{
    if(e.target.id==='demo-state')loadExample();
    if(e.target.id==='demo-role'){role=e.target.value;mine=false;close();render();announce(role==='teacher'?'Режим учителя: задачи только для просмотра.':'Режим ученика: вы владелец проекта.');}
    if(e.target.id==='demo-theme'||e.target.id==='panel-theme'){document.documentElement.dataset.theme=e.target.value;$('#demo-theme').value=e.target.value;writeUrl();}
    if(e.target.matches('[data-task-status]')&&role==='student'){const t=model.tasks.find(t=>t.id===Number(e.target.dataset.taskStatus));if(t){t.status=e.target.value;renderTasks();const same=$(`[data-task-status="${t.id}"]`);if(same)same.focus();else $('.done-list>summary')?.focus();announce('Статус задачи изменён в примере. Состояние этапа не изменилось.');}}
    if(e.target.matches('[data-filter]')){filters[e.target.dataset.filter]=e.target.value;renderTasks();}
  });
  $('#demo-reset').addEventListener('click',loadExample);
  document.addEventListener('submit',e=>{
    e.preventDefault();const form=e.target, data=new FormData(form);
    if(form.id==='task-form'&&role==='student'){
      const values={title:data.get('title').trim(),description:data.get('description').trim(),deadline:data.get('deadline'),assignee:data.get('assignee'),priority:data.get('priority'),stage:data.get('stage')?Number(data.get('stage')):null};
      if(!values.title){form.elements.title.setCustomValidity('Укажите название задачи.');form.elements.title.reportValidity();form.elements.title.addEventListener('input',()=>form.elements.title.setCustomValidity(''),{once:true});return;}
      if(form.dataset.edit)Object.assign(model.tasks.find(t=>t.id===Number(form.dataset.edit)),values);else model.tasks.push({...values,id:model.nextId++,status:'todo',reports:[]});
      close('[data-action="add-task"]');resetFilters();if(values.stage)selected=values.stage;else unassignedOpen=true;render();announce('Задача сохранена в примере.');
    }
    if(form.id==='report-form'&&role==='student'){
      const t=model.tasks.find(t=>t.id===activeTask),text=data.get('text').trim();if(!text){form.elements.text.setCustomValidity('Опишите результат работы.');form.elements.text.reportValidity();form.elements.text.addEventListener('input',()=>form.elements.text.setCustomValidity(''),{once:true});return;}if(t.assignee!=='alex')return;t.reports.push({who:'Алексей',date:'Сейчас · пример',text});renderTasks();showTask(activeTask);announce('Отчёт добавлен в пример.');
    }
    if(form.id==='result-form'&&role==='student'){
      try{if(e.submitter?.value==='submit')M.submit(model,selected,data.get('result'),data.get('presentation')||'');else M.save(model,selected,data.get('result'),data.get('presentation')||'');close('#result-card');render();announce(e.submitter?.value==='submit'?'Версия отправлена в примере. Переключите роль на учителя для проверки.':'Черновик сохранён в примере. Он ещё не отправлен.');}catch(error){$('#result-error').textContent=error.message;}
    }
    if(form.id==='review-form'&&role==='teacher'){
      try{M.decide(model,selected,e.submitter.value,data.get('comment'));render();$('#result-card').focus();announce(e.submitter.value==='accept'?'Этап принят в примере. Доступен следующий этап.':'В примере результат возвращён. Переключите роль на ученика.');}catch(error){$('#review-error').textContent=error.message;form.elements.comment.focus();}
    }
  });
  const params=new URLSearchParams(location.hash.slice(1));
  if(['student','teacher'].includes(params.get('role'))){role=params.get('role');$('#demo-role').value=role;}
  if([...$('#demo-state').options].some(o=>o.value===params.get('example')))$('#demo-state').value=params.get('example');
  model=M.create($('#demo-state').value);selected=M.current(model)||5;
  if(/^[1-5]$/.test(params.get('stage')||''))selected=Number(params.get('stage'));
  if(['light','dark'].includes(params.get('theme'))){$('#demo-theme').value=params.get('theme');document.documentElement.dataset.theme=params.get('theme');}
  new ResizeObserver(entries=>document.documentElement.style.setProperty('--preview-height',entries[0].target.getBoundingClientRect().height+'px')).observe($('.preview'));
  render();
})();
