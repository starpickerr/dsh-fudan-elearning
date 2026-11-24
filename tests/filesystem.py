import os


def link_directory(link, target):
    if os.name == 'nt':
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
