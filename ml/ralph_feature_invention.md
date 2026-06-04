# Ralph Mission: Autonomous AIS Derived-Feature Invention

You invent **new derived features** that raise the dcdetect anomaly-detection rate.
Do NOT just pick from the existing (fixed) `CANDIDATE_FEATURES` pool — **write new feature
code that is not in the pool**.

## Per-iteration procedure (follow exactly)

1. **Check past work**: read `ml/.ralph_fe_log.md` to see hypotheses/features already tried.
   Never repeat the same idea. If the file is empty, this is the first iteration.

2. **Read the run's own diagnosis (primary direction)**: read
   `ml/.pipeline_tmp/claude_fe_analysis.md` — the orchestrator's Claude analysis from the
   last converged run. Use its **weak-scenario list** and its **suggested feature *kinds***
   (analysis item 4) as the primary target. This grounds invention in real run data, not a
   guess. If the file is absent, fall back to the latest `feat_eng_iter*.json` weak
   scenarios, or these known weak ones: `D1-LowSlow`, `FN3-COG경계`, `G3-PhantomHDG`.

3. **Form a physical hypothesis**: pick ONE weak scenario (<50%) from step 2 and write 1-2
   sentences on how that attack differs physically from normal navigation, aligned with the
   feature *kind* the analysis suggested. (e.g. "at low speed, heading-vs-course mismatch is
   normal, so a linear feature can't catch it -> needs low-speed weighting".)

4. **Write ONE feature**: add exactly ONE `(description, lambda seq, t: ...)` entry to the
   `CANDIDATE_FEATURES` dict in `ml/core/feature_engineer.py`. Rules:
   - Index columns via `_B["sog"]` etc. BASE 12 features: sog, cog, heading, status, dt,
     dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed,
     lon_speed
   - Previous row is `seq[t-1]`. Always guard with `if t > 0 else 0.0`.
   - Prevent division by zero (`max(x, 1e-6)`); guard sentinels (`cog_hdg_diff < 0` means
     heading invalid).
   - Pure function (no side effects). Must be a genuinely NEW signal whose formula differs
     materially from existing features.
   - In a comment, state the target scenario + physical rationale.

5. **Validate**: run FE standalone (small data, fast):
   ```
   python ml/core/feature_engineer.py \
     --input D:/ais_data/preprocessed/ais_preprocessed_3yr.csv \
     --base_dir D:/ --max_mmsi 50 --epochs 1 --max_steps 1 \
     --out_json ml/.ralph_tmp.json
   ```
   In the candidate evaluation table, read the objective-score gain (the number after
   `▲`/`▼`) on **the row for the feature you added**.

6. **Decide + act**:
   - gain **>= +3.0pp**: success. Keep the feature.
     `git add ml/core/feature_engineer.py && git commit`
     (message: `feat(fe): ralph invent <feature_name> obj+<gain>`). Increment adopted count.
   - **0 to +3.0pp**: marginal. Keep the feature in the pool but do NOT count it as adopted.
     Log it as "marginal".
   - **< 0pp**: harmful. **Revert** the lambda you just added. Log the failed hypothesis.

7. **Update log**: append one line to `ml/.ralph_fe_log.md`:
   `iter N | <feature_name> | target=<scenario> | gain=<number>pp | <success/marginal/fail> | hypothesis=<summary>`

## Completion condition

When `git log --oneline | grep -c "ralph invent"` shows **3 successfully adopted features**:
print a final summary (the 3 adopted features: names, gains, targets) and output exactly:

`<promise>RALPH_FE_DONE</promise>`

## Forbidden

- Never output a false promise (forbidden before 3 genuine adoptions).
- Never run orchestrator.py (git branch-chaining collision). FE standalone only.
- Never add more than one feature per iteration. Exactly one.
- Do not change anything beyond the data file references and `CANDIDATE_FEATURES`.
