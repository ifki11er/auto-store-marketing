subject_definitions:
<Subject 1> is {{FACE_DESCRIPTION}} in <Picture 1>, the person appearing in the video whose identity, face, hairstyle, and body build come exclusively from <Picture 1>, replacing any person in {{GARMENT_PICTURE}}.
<Subject 2> is strictly the clothing shown in {{GARMENT_PICTURE}}, transferred as wearable items without the body, face, hair, pose, or identity of any person in that picture.
<Subject 3> is {{LOCATION}}.

summary:
[reference generation] <Subject 1> wears the clothing of <Subject 2> in <Subject 3>, preserving the identity from <Picture 1>.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - preserve the identity, face, hairstyle, and body build from <Picture 1>, with no blending from any person in {{GARMENT_PICTURE}}.
<Subject 2> (appears in [Shot 1]): attribute_transfer - transfer only the clothing exactly as shown in {{GARMENT_PICTURE}} onto <Subject 1>, excluding the original wearer's body and identity.
{{LOCATION_RETENTION}}

detailed_description:
The target video uses a realistic live-action style.
[Shot 1] In <Subject 3>, <Subject 1> is {{FACE_DESCRIPTION}}. <Subject 1> wears <Subject 2> and {{ACTION}}. The clothing fits naturally on <Subject 1>, whose face, hairstyle, and body build from <Picture 1> remain consistent throughout. Keep the person's full body visible throughout. Clean screen without subtitles. Duration: {{SECONDS}} seconds.

overall_soundscape: Silence throughout.
non_diegetic_music: N/A.
