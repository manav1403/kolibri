import json
import os
import re
from itertools import compress

import requests
from django.core.cache import cache
from django.utils.text import compress_string

from kolibri.core.content.models import LocalFile
from kolibri.core.content.utils.paths import get_content_storage_dir_path
from kolibri.core.content.utils.paths import get_file_checksums_url


checksum_regex = re.compile("^([a-f0-9]{32})$")


def _filter_checksums_by_channel(checksums, channel_id):
    """
    Helper function to filter checksums received to what is actually used by our
    copy of the channel. Used by both module methods, as for a disk based import
    there could be many files present that are not part of the channel, and for
    remote import, the remote channel might be a different version and hence
    have different local files associated with it.
    """
    return list(
        set(
            LocalFile.objects.filter(
                files__contentnode__channel_id=channel_id
            ).values_list("id", flat=True)
        ).intersection(set(checksums))
    )


def _generate_mask(integer_mask):
    while integer_mask:
        yield bool(integer_mask % 2)
        integer_mask /= 2


def get_available_checksums_from_remote(channel_id, baseurl):
    CACHE_KEY = "PEER_AVAILABLE_CHECKSUMS_{baseurl}_{channel_id}".format(
        baseurl=baseurl, channel_id=channel_id
    )
    if CACHE_KEY not in cache:

        channel_checksums = (
            LocalFile.objects.filter(
                available=False, files__contentnode__channel_id=channel_id
            )
            .values_list("id", flat=True)
            .distinct()
        )

        response = requests.post(
            get_file_checksums_url(channel_id, baseurl),
            data=compress_string(
                bytes(json.dumps(list(channel_checksums)).encode("utf-8"))
            ),
            headers={"content-type": "application/gzip"},
        )

        checksums = None

        # Do something if we got a successful return
        if response.status_code == 200:
            try:
                integer_mask = int(response.content)

                # Filter to avoid passing in bad checksums
                checksums = list(
                    compress(channel_checksums, _generate_mask(integer_mask))
                )
                cache.set(CACHE_KEY, checksums, 3600)
            except (ValueError, TypeError):
                # Bad JSON parsing will throw ValueError
                # If the result of the json.loads is not iterable, a TypeError will be thrown
                # If we end up here, just set checksums to None to allow us to cleanly continue
                pass
    else:
        checksums = cache.get(CACHE_KEY)
    return checksums


def get_available_checksums_from_disk(channel_id, basepath):
    PER_DISK_CACHE_KEY = "DISK_AVAILABLE_CHECKSUMS_{basepath}".format(basepath=basepath)
    PER_DISK_PER_CHANNEL_CACHE_KEY = "DISK_AVAILABLE_CHECKSUMS_{basepath}_{channel_id}".format(
        basepath=basepath, channel_id=channel_id
    )
    if PER_DISK_PER_CHANNEL_CACHE_KEY not in cache:
        if PER_DISK_CACHE_KEY not in cache:
            content_dir = get_content_storage_dir_path(datafolder=basepath)

            disk_checksums = []

            for _, _, files in os.walk(content_dir):
                for name in files:
                    checksum = os.path.splitext(name)[0]
                    # Only add valid checksums formatted according to our standard filename
                    if checksum_regex.match(checksum):
                        disk_checksums.append(checksum)
            # Cache is per device, so a relatively long lived one should
            # be fine.
            cache.set(PER_DISK_CACHE_KEY, disk_checksums, 3600)
        else:
            disk_checksums = cache.get(PER_DISK_CACHE_KEY)
        checksums = _filter_checksums_by_channel(disk_checksums, channel_id)
        cache.set(PER_DISK_PER_CHANNEL_CACHE_KEY, checksums, 3600)
    else:
        checksums = cache.get(PER_DISK_PER_CHANNEL_CACHE_KEY)
    return checksums
