import csv
import io
from fastapi import Response


def csv_response(filename: str, columns: list[str], rows) -> Response:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    def safe(value):
        text = "" if value is None else str(value)
        return f"'{text}" if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@")) else text
    writer.writerow(columns)
    writer.writerows([safe(value) for value in row] for row in rows)
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})
