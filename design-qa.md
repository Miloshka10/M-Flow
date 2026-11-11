# Linear / interactive orbit — local release QA

Date: 2026-11-10. Scope: optional authenticated `/next` view and shared orbit interactions. Deployment verification is separate from this local visual result.

## Evidence and normalization

Visual truth: the user-approved M-Flow Linear prototype, `../../2026-11-07/new-chat/outputs/mflow-linear/qa/orbit/project-1440.png` in the Codex workspace. Rendered implementation: `.tmp/qa/first-desktop.png`; viewed together in `.tmp/qa/comparison-desktop.jpg` and the native-scale shell/title crop `.tmp/qa/comparison-focus.jpg`.

Both CSS viewports were 1440 × 1024 at DPR 1. Source PNG 1425 × 1013, server PNG 1440 × 1024; the browser's screenshot scaling/scrollbar differs. Both were normalized to the same CSS frame before pairing. Both show two accepted stages, current returned stage 3, and the same project title. Actual user names, project IDs, timestamps and native action destinations differ intentionally; these fields now come from the server. Animated pose/seed also differs.

Additional actual captures: `.tmp/qa/mobile-375.png` (375 × 900), `tablet-768.png` (768 × 900), `compact-1280.png` (1280 × 900), `desktop-1440.png` and `home-mobile.png`. The later captures show three accepted stages after the disposable acceptance walkthrough; this is an intentional state change rather than visual drift. Images remain local because they contain synthetic fixture records and are not part of the shipped assets.

## Required fidelity surfaces

- Fonts: local Inter Variable retains 20 px/590 workspace headings, compact navigation and 12–14 px body hierarchy. Secondary coordinate text uses system monospace in the published view. Verified the focused paired title/navigation crop.
- Spacing: same 232 px desktop sidebar, compact toolbars, article/inspector grid and restrained 340 px scene (280 px phone). Mobile/tablet action is visible in the toolbar. No tested page overflow or clipped tabs at widths 375, 768, 1280 and 1440.
- Color/tokens: same dark surfaces, thin borders, muted metadata and one violet accent. Orbit geometry retains silver/violet tones; focus and hover use restrained rings, not a different palette.
- Assets: shared projected Canvas geometry derives from accepted/current stages; no replacement bitmap or remote asset is used. Local Inter has its license. The new view uses the existing production icon set and actual presentation links instead of the demo catalogue's artwork.
- Copy: actual accessible projects, tasks, history, comments and stage states. Empty text is role-aware. The new view explains that editing opens the existing form; no simulated mutation or misleading role picker is shipped.

## Findings, fixes and rechecks

- P2: browser Back after selecting a node could leave the wrong stage selected when the earlier URL omitted `stage`. Fixed fallback to the server-selected stage; browser recheck restored returned stage 3 and the correct action.
- P2: mobile/tablet primary action would have required scrolling to the inspector. Added compact native action links to the existing toolbar; verified at 375 and 768 px.
- P2: tab links retained the original stage after client navigation. Update their real hrefs as well as UI state; verified node selection updates the task link and URL. Server-rendered task/history links also work without JavaScript.

No remaining P0/P1/P2 finding in the inspected scope. The read-only presentation is intentionally a first increment: editing/review, defense and assessment continue in existing native screens. The native project history is summarized in the History tab rather than duplicating it below the orbit.

## Functional verification

- Shared prototype and server renderer: rotation using arrows, reset, node navigation, hover/focus status. A renderer unit test exercises drag while paused, pointer capture/release, keyboard reset, projected node count, observer/listener cleanup and rotation bounds.
- Phone navigation dialog and project search, including no matches; stage selection and browser Back.
- On a disposable SQLite database: edit and save a returned result in the real native editor; reload the new view and observe the saved text with unchanged accepted count; submit; teacher opens the real review form; accept; reload shows 3 accepted stages and only the next stage open. No production records were used for this walkthrough.
- Login/membership guards, no GET writes, safe escaping, hidden unsent teacher draft, refusal of POSTs to the new read-only routes, existing CSRF behavior and native form destinations have dedicated tests; equivalent PostgreSQL cases run in CI.
- Local full suite: 755 tests reported OK, 312 skipped for unavailable local PostgreSQL/platform capabilities. After the final tab fallback change, the focused new-view suite reported 16 cases OK, 8 PostgreSQL skips. All 13 JavaScript suites passed. The updated local prototype also built and passed its 17 workflow/packaging checks. GitHub CI must pass the final source on Python 3.12 and 3.14 with disposable PostgreSQL before merge.
- No browser console warnings/errors in the inspected server view. Physical touch hardware and authenticated production behavior were not tested locally.

Previous result — passed.

## Единое оформление и темы · 11 ноября 2026

Визуальный ориентир — рабочий экран «Мост будущего» в `.tmp/theme-qa/before/next.png`. Исходный вход — `.tmp/theme-qa/before/login.png`. Итоговые снимки — `.tmp/theme-qa/after/next-final.png` и `.tmp/theme-qa/after/login-final.png`. Все четыре снимка имеют размер 1280 × 900 px при таком же CSS-viewport. Для общего сравнения каждый кадр уменьшен до 640 × 450 px в `.tmp/theme-qa/comparison.png`. В `.tmp/theme-qa/comparison-focus.png` навигация, вкладки и длинное название на телефоне сопоставлены в масштабе 1:1. Мобильные исходники имеют размер 375 × 812 px. В сравнении использованы одинаковые роли, проект и состояние этапа. Положение движущихся узлов на снимках может различаться.

- На входе поле пароля и восстановление видны без дополнительного блока текста. Справа статичная орбита, на телефоне форма занимает всю ширину. Проверены роль, видимость пароля и вход через обычную форму.
- При ширине 375 px название из 80 букв занимало большую часть рабочего блока. Теперь на главной видно до двух строк, полное название доступно на странице проекта по той же ссылке. На ширине 768 px карточки разной высоты заменены строками с прогрессом. Итоговый снимок — `.tmp/theme-qa/after/studentProjects-final-tablet.png`.
- На 320 px в обзоре проекта оставалась горизонтальная прокрутка. После изменения минимальной ширины измерены 305 px и для области просмотра, и для содержимого. Проверены длинное название, смена этапа, вкладка «История», возврат и поворот орбиты с клавиатуры.
- Проверены обе темы на входе, в рабочем пространстве и в обзоре проектов. Светлая тема сохранилась при перезагрузке и входе. Выключение анимаций в настройках остановило орбиту. Кнопка сброса вернула тёмную тему.
- На локальных данных проверены кабинеты обеих ролей, включая мобильную очередь и открытие формы решения, восстановление, почта профиля, группы, команды, настройки, история и защита. Ширины 320, 375, 768 и 1280 px. В итоговых браузерных проверках отсутствовали ошибки и предупреждения консоли.
- Локально пройдены 13 наборов JavaScript и 757 Python-тестов с 313 пропусками PostgreSQL и проверок окружения. Браузерные проверки ролей проведены на тестовых аккаунтах в эмуляции размеров экрана.

final result: passed
