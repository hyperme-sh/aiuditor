"""Verify GHCR access using only an anonymously issued pull token."""
import json
import os
import urllib.parse
import urllib.request


def check(repository):
    image = repository.lower() + '/runtime'
    query = urllib.parse.urlencode({'service':'ghcr.io','scope':f'repository:{image}:pull'})
    with urllib.request.urlopen('https://ghcr.io/token?' + query, timeout=30) as response:
        token = json.load(response)['token']
    request = urllib.request.Request(f'https://ghcr.io/v2/{image}/manifests/test',headers={
        'Authorization':'Bearer ' + token,
        'Accept':'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError('Anonymous container access failed')
    print(f'Anonymous access verified: ghcr.io/{image}:test')


if __name__ == '__main__':
    check(os.environ['GITHUB_REPOSITORY'])
