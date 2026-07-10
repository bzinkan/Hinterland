# Retired Environment Retirement

The Hinterland Azure environment is the only runtime. This runbook governs
retirement of the former environment after the rebranded API, websites, and
fresh mobile package have passed acceptance.

## Acceptance Gate

Record a release ticket containing all of the following before retirement:

1. `health`, `ready`, and the Hinterland kid JWKS endpoint return 200.
2. Azure API, Static Web Apps, and content-sync deployment checks are green.
3. Adult sign-in, kid handoff, photo upload and identification, Field Journal,
   and Expedition pass from the fresh mobile package.
4. The former environment inventory is attached to the ticket, including
   database, blob storage, DNS, app registrations, mobile artifacts, and
   deployment credentials.

## Backup And Verification

1. Export the former database and blobs to an encrypted offline archive.
2. Record archive checksums, the encryption-key custody location, and the
   export manifest outside this repository.
3. Restore the archive into an isolated verification target and record the
   successful integrity check. Do not import that data into Hinterland.
4. Set a retention deadline exactly 30 days after verification. Access is
   restricted to the designated operator during that window.

## Retirement

After the retention deadline, securely delete the verified offline archive and
its temporary restore target. Then remove the former DNS and deployments, app
registrations, cloud resources, Play artifacts, EAS project association, and
deployment secrets. Record completion in the release ticket.
