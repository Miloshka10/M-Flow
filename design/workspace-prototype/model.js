/* Prototype-only in-memory fixtures. No requests, storage, application imports or DB. */
(function (root) {
  'use strict';
  const names = ['Выбор темы и согласование с учителем', 'Работа над проектом — 1', 'Работа над проектом — 2', 'Работа над проектом — 3', 'Подготовка документации'];
  const shortNames = ['Тема проекта', 'Работа · 1', 'Работа · 2', 'Работа · 3', 'Документация'];
  const goals = ['Сформулируйте тему, цель и ожидаемый результат. Согласуйте их с учителем.', 'Опишите первый результат работы и выбранные способы решения задачи.', 'Покажите развитие проекта: что сделано, проверено и что осталось улучшить.', 'Подготовьте результат работы и объясните, как он решает задачу проекта.', 'Подготовьте описание проекта и доступную учителю ссылку на презентацию.'];
  const sampleResults = [
    'Создадим систему автоматического полива для школьного сада. Датчик измеряет влажность почвы, а контроллер включает насос только тогда, когда это необходимо. Проверим, помогает ли система поддерживать стабильную влажность.',
    'Выбрали датчик влажности и контроллер. Собрали схему подключения, сравнили два варианта полива и составили план испытаний. Для проверки используем три одинаковых контейнера с растениями.',
    'Собрали работающий прототип полива. Датчик передаёт показания раз в минуту. При низкой влажности включается насос.\n\nПровели пробный запуск в трёх контейнерах: система работает, но порог включения пока подобран приблизительно.\n\nЖурнал испытаний: https://example.org/greenhouse-log',
    'Провели серию испытаний и уточнили порог влажности. Полив включается по показаниям датчика. Описали ограничения системы и подготовили итоговый прототип.',
    'Проект «Умная теплица»: автоматический полив для школьного сада. В описании собраны цель, схема, методика испытаний, результаты и выводы. Презентация подготовлена для защиты.'
  ];
  const comment = 'Добавьте таблицу измерений за три дня и объясните, почему выбран именно этот порог влажности. Нужны данные, по которым можно проверить вывод.';
  const priorities = {low:'Низкий',normal:'Обычный',high:'Высокий',urgent:'Срочный'};
  const statuses = {todo:'Не начата',progress:'В работе',done:'Выполнена'};
  const people = {alex:'Алексей · вы',dasha:'Дарья',max:'Максим','':'Не назначен'};
  function create(scenario='returned') {
    const empty = scenario === 'empty', complete = scenario === 'completed', accepted = scenario === 'accepted';
    const stages = names.map((name,i) => ({number:i+1,name,short:shortNames[i],goal:goals[i],state: complete || i<2 || (accepted && i===2) ? 'done' : i===2 && !empty ? 'progress' : 'todo',result:complete || i<2 || (i===2 && !empty) ? sampleResults[i] : '',comment:'',reviewed:false,version:1,presentation:i===4 && complete ? 'https://example.org/greenhouse-presentation' : '',history:[]}));
    if(empty) stages.forEach(s=>Object.assign(s,{state:'todo',result:''}));
    const target=stages[2];
    if(['review','resubmitted'].includes(scenario)) target.state='review';
    if(['returned','resubmitted'].includes(scenario)) {target.comment=comment;target.reviewed=true;}
    if(scenario==='resubmitted') {target.version=2;target.result=sampleResults[2]+'\n\nДобавлена таблица измерений за три дня. Порог включения определили по средним значениям для контрольной группы.';}
    stages.forEach(s=>{
      if(s.state==='done') s.history.push({type:'accepted',version:1,text:s.result,comment:'Результат принят. Можно продолжать работу.',who:'Мария Сергеевна',date:'23 октября, 14:20'});
      if(s.number===3 && ['returned','resubmitted'].includes(scenario)) s.history.push({type:'returned',version:1,text:sampleResults[2],comment,who:'Мария Сергеевна',date:'25 октября, 16:10'});
      if(s.state==='review') s.history.push({type:'submitted',version:s.version,text:s.result,comment:'',who:'Алексей',date:'26 октября, 09:40'});
    });
    const tasks=empty ? [] : [
      {id:1,title:'Собрать таблицу измерений за три дня',description:'Запишите влажность утром и вечером. Добавьте время включения насоса и сравните показатели трёх контейнеров.',assignee:'alex',deadline:'2026-10-27',priority:'high',stage:3,status:'progress',reports:[{who:'Алексей',date:'26 октября, 10:15',text:'Данные за первые два дня внесены. Осталось провести вечернее измерение.'}]},
      {id:2,title:'Обосновать порог включения полива',description:'Сравните данные и объясните выбор порога. Вывод должен опираться на измерения.',assignee:'dasha',deadline:'2026-10-28',priority:'normal',stage:3,status:'todo',reports:[]},
      {id:3,title:'Проверить работу датчика',description:'Сравнить показания в сухой и влажной почве.',assignee:'max',deadline:'2026-10-25',priority:'normal',stage:3,status:'done',reports:[{who:'Максим',date:'25 октября, 12:30',text:'Датчик проверен. Ошибок при повторных измерениях не обнаружили.'}]},
      {id:4,title:'Собрать схему подключения',description:'Подключить датчик, контроллер и насос.',assignee:'alex',deadline:'2026-10-22',priority:'normal',stage:2,status:'done',reports:[]},
      {id:5,title:'Сформулировать цель проекта',description:'Обсудить ожидаемый результат с командой.',assignee:'alex',deadline:'2026-10-20',priority:'normal',stage:1,status:'done',reports:[]},
      {id:6,title:'Подготовить итоговый прототип',description:'Собрать компоненты в корпус и описать ограничения.',assignee:'max',deadline:'2026-10-30',priority:'normal',stage:4,status:'todo',reports:[]},
      {id:7,title:'Собрать презентацию проекта',description:'Добавить цель, схему, результаты испытаний и выводы.',assignee:'dasha',deadline:'2026-11-02',priority:'normal',stage:5,status:'todo',reports:[]},
      {id:8,title:'Обсудить оформление стенда',description:'Выбрать фотографии и подготовить подписи.',assignee:'',deadline:'',priority:'low',stage:null,status:'todo',reports:[]}
    ];
    if(complete) tasks.forEach(t=>t.status='done');
    if(accepted) tasks.filter(t=>t.stage===3).forEach(t=>t.status='done');
    return {scenario,stages,tasks,hasTeacher:scenario!=='no-teacher',defense:complete?'accepted':'draft',published:complete,nextId:9};
  }
  function current(model){return model.stages.find(s=>s.state!=='done')?.number || null;}
  function label(model,s){if(s.state==='done')return 'Принят учителем';if(s.number>(current(model)||5))return 'После этапа '+(s.number-1);if(s.state==='review')return s.version>1?'Повторная проверка':'На проверке';if(s.reviewed)return 'Нужна доработка';return s.state==='todo'?'Не начат':'В работе';}
  function canEdit(model,s,role){return role==='student' && s.number===current(model) && ['todo','progress'].includes(s.state);}
  function save(model,number,text,presentation='') {
    const s=model.stages[number-1];
    if(!canEdit(model,s,'student')) throw Error('Этот этап сейчас нельзя изменять.');
    s.result=text.trim();s.presentation=presentation.trim();s.state='progress';
  }
  function submit(model,number,text,presentation='') {
    if(!model.hasTeacher) throw Error('Сначала нужно согласовать подключение руководителя.');
    if(!text.trim()) throw Error('Опишите результат этапа.');
    if(number===5 && !/^https?:\/\/[^\s]+$/i.test(presentation.trim())) throw Error('Добавьте полную HTTP(S)-ссылку на презентацию.');
    save(model,number,text,presentation);
    const s=model.stages[number-1];
    s.version=s.history.length?Math.max(...s.history.map(h=>h.version))+1:1;
    s.state='review';s.history.push({type:'submitted',version:s.version,text:s.result,comment:'',who:'Алексей',date:'Сейчас · пример'});
  }
  function decide(model,number,decision,feedback) {
    const s=model.stages[number-1];
    if(s.state!=='review' || number!==current(model)) throw Error('Можно проверить только отправленный текущий этап.');
    if(decision==='return' && !feedback.trim())throw Error('Напишите, что нужно исправить.');
    s.state=decision==='accept'?'done':'progress';s.comment=feedback.trim();s.reviewed=true;
    s.history.push({type:decision==='accept'?'accepted':'returned',version:s.version,text:s.result,comment:s.comment,who:'Мария Сергеевна',date:'Сейчас · пример'});
  }
  function next(model,s,role) {
    const cur=current(model);
    if(s.state==='done')return {kind:'accepted',title:cur?'Этап принят. Результат сохранён.':model.defense==='accepted'&&model.published?'Проект завершён':model.defense==='accepted'?'Защита принята. Оценка ещё не опубликована.':'Пять этапов приняты',text:cur?'Здесь можно посмотреть принятую работу и решение учителя.':model.defense==='accepted'&&model.published?'Видеозащита принята. Итоговые оценки опубликованы.':model.defense==='accepted'?'Принятие защиты не публикует оценку автоматически.':'Видеозащита и итоговое оценивание проходят отдельно.',action:cur?'current':'finish',button:cur?'К текущему этапу':'Защита и результат'};
    if(s.number>cur)return {kind:'',title:'Результат откроется после принятия этапа '+(s.number-1),text:'Планировать задачи этого этапа можно уже сейчас.',action:'current',button:'К текущему этапу'};
    if(!model.hasTeacher)return {kind:'',title:'Подключите руководителя проекта',text:'Готовьте задачи и черновик. Отправить результат можно после подтверждения сопровождения.',action:'supervisor',button:'Руководитель проекта'};
    if(s.state==='review')return role==='teacher'?{kind:'review',title:s.version>1?'Проверьте исправленный результат':'Результат ждёт вашего решения',text:'Прочитайте работу, затем примите этап или верните его с замечанием.',action:'result',button:'Перейти к проверке'}:{kind:'review',title:s.version>1?'Исправленная работа у учителя':'Результат у учителя',text:'До решения результат нельзя менять. Задачи и отчёты команды доступны.',action:'result',button:'Посмотреть отправку'};
    if(s.reviewed)return {kind:'returned',title:role==='teacher'?'Команда дорабатывает результат':'Доработайте результат этапа',text:role==='teacher'?'Новое решение станет доступно после повторной сдачи.':'Учитель вернул работу. Учтите замечание и отправьте новую версию.',action:role==='teacher'?'result':'edit-result',button:role==='teacher'?'Посмотреть замечание':'Доработать результат'};
    return role==='teacher'?{kind:'',title:'Ученики готовят результат',text:'Пока можно посмотреть задачи и отчёты. Проверка откроется после отправки.',action:'tasks',button:'Посмотреть задачи'}:{kind:'progress',title:s.result?'Подготовьте результат к отправке':s.number===1?'Согласуйте тему проекта':'Начните работу над этапом',text:s.result?'Проверьте черновик и отправьте его руководителю.':'Опишите, что вы планируете сделать и какой результат хотите получить.',action:'edit-result',button:s.result?'Открыть черновик':'Подготовить результат'};
  }
  root.WorkspaceModel={create,current,label,canEdit,save,submit,decide,next,statuses,priorities,people};
  if(typeof module!=='undefined')module.exports=root.WorkspaceModel;
})(globalThis);
