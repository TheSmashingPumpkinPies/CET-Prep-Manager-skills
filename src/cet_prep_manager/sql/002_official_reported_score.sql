ALTER TABLE exam_attempt
ADD COLUMN official_reported_score INTEGER
CHECK (
    official_reported_score IS NULL
    OR (
        attempt_type = 'official_exam'
        AND official_reported_score BETWEEN 0 AND 710
    )
);
