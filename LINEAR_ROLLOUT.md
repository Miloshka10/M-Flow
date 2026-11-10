# Linear interface: first incremental release

`/next` lists the signed-in user's actual projects. `/next/project/<id>` adds the approved dark workspace, five stages, actual result/tasks/history, feedback and an interactive project structure. The existing navigation exposes **Новый вид**; the current interface remains available.

This release deliberately keeps mutations in the existing working forms. Student edit and teacher review links open the native stage editor with its normal CSRF, revision and permission checks. There is no demo role switch, client-side acceptance, new write API, schema migration or platform configuration change. The published view never serves the local prototype's synthetic project catalogue. An unsubmitted draft is withheld from teachers in this new presentation.

Drag the structure, use arrows when the canvas has keyboard focus, or reset its orientation. Primary orbit nodes navigate to existing accessible stages and show their state on hover/focus. Future stages remain locked. Automatic animation can be paused, respects reduced motion, and stops offscreen or in hidden documents. Pointer drag, keyboard input, projection and cleanup use the same renderer as the local React prototype.

The first release moves project browsing and progress visualization. Full editing inside the new layout, the marketing homepage, account pages and the teacher review queue are later increments; current links keep those flows working now. Defense and assessment remain independent of stage acceptance.

Validation: local role/security/read-only regression tests, all JavaScript suites, a disposable end-to-end student save → submit → teacher accept → updated orbit run, desktop/tablet/phone screenshots, and GitHub's Python 3.12/3.14 + PostgreSQL CI before merge. See `design-qa.md` for visual evidence and local limitations.

Render continues to use its existing Python build/start settings. No Node build is required: the new JavaScript is served as native modules and all assets are local. Inter is redistributed with its license; secondary coordinates use system monospace.

Rollback is a normal revert of this isolated presentation change. It requires no database rollback, because the release adds no schema or stored-state changes. Deploy only after CI passes, then verify Render reaches `live`, `/health` responds and released asset hashes match the reviewed commit. Public endpoint verification does not substitute for authenticated production testing.
