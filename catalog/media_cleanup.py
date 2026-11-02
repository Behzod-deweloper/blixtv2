from pathlib import PurePosixPath

from botocore.exceptions import ClientError
from django.core.files.storage import default_storage


def delete_storage_tree(directory):
    try:
        directories, files = default_storage.listdir(directory)
    except FileNotFoundError:
        return
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            return
        raise

    for name in files:
        default_storage.delete(str(PurePosixPath(directory) / name))
    for name in directories:
        delete_storage_tree(str(PurePosixPath(directory) / name))
