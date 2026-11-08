# Kazakh Cyrillic pilot

Revision `kk-rta-m1-1` translates the `rta-module-1` profile from English commit
`7e4ef9f5e9752f2812385fd555ff1cf0acea443e`. The current-session LLM translated
and separately checked all 356 required units: 226 UI messages, 44 Quiz leaves,
32 lesson sections, 25 Lab Markdown cells, 13 course-guide sections, 12 metadata
leaves and four Quiz Notebook cells.

[overlay.json](overlay.json) binds every translation and check to the permanent
unit ID and exact source/target hashes. [revision.json](revision.json) records
batches, model information, corrections and the scope of the second comparison.
The translations have state `llm_checked`. Native human review is unperformed.
The shared [glossary](../glossary.json) pins product names and Kazakh terminology.

The six diagrams retain their original English PNGs. Each translated lesson
image has a Kazakh alt, caption and complete adjacent label mapping.
[image-register.json](image-register.json) records each frozen source hash,
legend unit, treatment rationale, available source and repository license.
The producer explicitly admits these as `shared_original`; their raster text
remains English. The editable diagram sources are absent from the frozen tree.

The full course README is translated, including navigation to other modules
and optional work. The selected profile covers Module 1's primary lesson, Lab
and Quiz. The producer marks English navigation mirrors explicitly. Metabase's
optional Notebook and the other modules require their own translation scope.
Local previews passed at 1440, 390 and 320 pixels, with a real six-question
Jupyter submission. The UI keeps its `Manrope,sans-serif` stack: Manrope omits
some Kazakh letters, and the local browser used Helvetica fallback for them.
Preserve the system fallback.

Code cells, machine values, identities, grading policy and answer IDs retain
their canonical source. Lab code output strings remain as authored in English.

Build a private bilingual teaching package using the producer documented in
[locales/README.md](../README.md), selecting `--profile rta-module-1 --locale en
--locale kk-Cyrl`. Commit the inputs first and pass the full resulting SHA.
The teaching tree contains source courses and answers; use its answer-free
public projection for public Quiz delivery. Workshop installation and website
integration follow their separate consumer workflows.
