subject_definitions:
<Subject 1> is {{FACE_DESCRIPTION}} in <Picture 1>, the person appearing in the video whose identity, face, hairstyle, and body build come exclusively from <Picture 1>, replacing any person in {{GARMENT_PICTURE}}.
<Subject 2> is the sale garment in {{GARMENT_PICTURE}}: {{GARMENT_DESCRIPTION}}. Transfer the clothing without the body, face, hair, pose, or identity of any person in that picture.
<Subject 3> is {{LOCATION}}.

summary:
[reference generation] <Subject 1> wears the clothing of <Subject 2> in <Subject 3>, preserving the identity from <Picture 1>.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - preserve the identity, face, hairstyle, and body build from <Picture 1>, with no blending from any person in {{GARMENT_PICTURE}}.
<Subject 2> (appears in [Shot 1]): attribute_transfer - preserve the sale garment exactly as shown in {{GARMENT_PICTURE}}, including its colors, transparency, fabric texture, sheen, thickness, weave, and details. Preserve other clothing visible on the wearer as shown.
{{LOCATION_RETENTION}}

detailed_description:
The target video uses a realistic live-action style.
[Shot 1] In <Subject 3>, <Subject 1> is {{FACE_DESCRIPTION}}. <Subject 1> wears <Subject 2>, {{GARMENT_DESCRIPTION}} from {{GARMENT_PICTURE}}, and {{ACTION}}. {{OUTFIT_DESCRIPTION}} {{ACCESSORIES}} The clothing fits naturally on <Subject 1>, whose face, hairstyle, and body build from <Picture 1> remain consistent throughout. Keep the person's full body visible throughout. Clean screen without subtitles. Duration: {{SECONDS}} seconds.

overall_soundscape: Silence throughout.
non_diegetic_music: N/A.
