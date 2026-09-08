ALTER TABLE exam_attempt
ADD COLUMN official_listening_score INTEGER
CHECK (
    official_listening_score IS NULL
    OR (attempt_type = 'official_exam' AND official_listening_score BETWEEN 0 AND 249)
);

ALTER TABLE exam_attempt
ADD COLUMN official_reading_score INTEGER
CHECK (
    official_reading_score IS NULL
    OR (attempt_type = 'official_exam' AND official_reading_score BETWEEN 0 AND 249)
);

ALTER TABLE exam_attempt
ADD COLUMN official_writing_translation_score INTEGER
CHECK (
    official_writing_translation_score IS NULL
    OR (
        attempt_type = 'official_exam'
        AND official_writing_translation_score BETWEEN 0 AND 212
    )
);
