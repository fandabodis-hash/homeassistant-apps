# TNG IQ FANDA – verified good production runtime checkpoint

This commit preserves the **exact running frontend container webroot** that was visually confirmed by the user as the desired working state.

Capture source:

- container: iqfanda-frontend
- path: /usr/share/nginx/html
- index SHA256: $IndexSha
- archive SHA256: $ActualArchiveSha
- file count: $FileCount
- script references: $ScriptCount
- stylesheet references: $StyleCount
- public index equals container index: PASS

Safety:

- production changed by this checkpoint: NO
- database changed: NO
- backend changed: NO
- Agent changed: NO
- original PC1 working tree committed: NO

webroot.tar.gz is the byte-for-byte recovery artifact for this exact known-good runtime.

This checkpoint intentionally preserves runtime separately from normal frontend source. Source reconciliation and a later merge can be done after the working runtime is safely protected in Git.