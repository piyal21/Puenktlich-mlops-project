"""`make upload-sample-days`: copy the 9 sample silver days from MinIO to the AWS data bucket."""

from dbdelay.serving.samples import upload_main

if __name__ == "__main__":
    raise SystemExit(upload_main())
