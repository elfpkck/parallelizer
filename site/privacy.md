# Privacy note: problem reports

Parallelizer never sends anything on its own. It keeps a small local log (no layer names, file paths or coordinates) and, when you choose **Report a problem…**, shows you a report you can read and edit before deciding what to do with it.

## What a report contains

Software versions (plugin, QGIS, Qt, Python, GEOS, PROJ, GDAL), your operating system, locale and screen scaling, other active plugins, the plugin's settings, the last error, a description of the layers, project and features the last few operations worked on (types, coordinate systems, counts, validity), recent plugin log lines and, if QGIS crashed or froze last time, the Python stacks it left. Paths to your home and QGIS profile folders are replaced. Geometries are only added if you tick **Include the geometries involved**, and then they are moved to a local origin so their real location is not included. You can edit or delete anything before sending.

## Ways to share it

- **Send report**: the text in the dialog is sent to the developer's report service, a [Cloudflare](https://www.cloudflare.com/privacypolicy/) Worker that files it as an issue in a private GitHub repository only the developer can see. Your IP address is used only to limit how many reports can be sent per minute; it is not stored or logged by the service. Cloudflare and GitHub process the data on the developer's behalf.
- **Open GitHub issue**: a pre-filled issue opens in your browser. It is only created if you submit it, under your GitHub account, and GitHub issues are public.
- **Send by email**: a pre-filled message opens in your own mail app and is only sent if you send it; the developer then receives your email address with it.
- **Copy to clipboard**: nothing is sent.

## Retention and your rights

Reports sent with **Send report** are deleted once the problem is handled, and at the latest after 12 months. The dialog shows a reference number after sending; to have a report deleted earlier, or to ask what was received, open an issue on [GitHub](https://github.com/elfpkck/parallelizer/issues) quoting the reference (don't include the report itself), or write to the email address shown in the plugin's report dialog.
