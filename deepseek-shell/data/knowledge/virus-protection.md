# Virus protection: ClamAV, VirusTotal, ANY.RUN
Keywords: virus, antivirus, malware, clamav, scan, virustotal, any.run, anyrun, sandbox, quarantine, infected, trojan, signatures, safe, exe check

- App: "ClamAV Security" in the Start menu (scans, protection status, API keys, quarantine, history).
- Scan from the command line: `myos-scan --cli PATH` (file or folder).
- Every .exe/.msi is checked the first time it runs: ClamAV always; VirusTotal (hash lookup) and
  ANY.RUN when their API keys are entered in ClamAV Security > Windows programs. Later starts go
  straight to Wine/Proton, until the file changes (an update is checked again once). A file found
  dangerous is checked every time. Checked programs: ~/.local/state/myos/checked-programs.json.
- To check a program again now: right-click it > Windows program > Scan for viruses, or
  `myos-scan --cli FILE.exe`. To check at every start: ClamAV Security > Windows programs >
  "Check them again every time they start".
- Signatures update automatically (service clamav-freshclam). Check: `systemctl status clamav-freshclam --no-pager`
  and the date in `clamscan --version`. Force an update (admin): `systemctl restart clamav-freshclam`.
- The scanner service starts on demand: `systemctl status clamav-daemon --no-pager`.
- Quarantined files: ~/.local/share/myos/quarantine (restore or delete them in the app).

## Keys
- VirusTotal: free account at https://www.virustotal.com, then the API key under the profile menu.
  Free keys allow 4 lookups per minute and 500 per day.
- ANY.RUN: needs an ANY.RUN plan with API access (Profile > API and Limits). Without a key the
  app can still open the ANY.RUN website for a manual check.
The user must create these accounts and paste the keys themselves.
