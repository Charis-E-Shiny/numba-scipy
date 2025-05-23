import logging
import collections
import ctypes
import re

import numba
import scipy.special.cython_special as cysc
from numba.extending import get_cython_function_address

CYTHON_TO_NUMBA = {
    'double': numba.types.float64,
    'float': numba.types.float32,
    'long': numba.types.long_,
}

NUMBA_TO_CTYPES = {
    numba.types.float64: ctypes.c_double,
    numba.types.float32: ctypes.c_float,
    numba.types.long_: ctypes.c_long,
}

logger = logging.getLogger("numba_scipy.special.signatures")


def parse_capsule_name(capsule):
    logger.debug(f"Parsing capsule name: {calsule}")
    # There isn't a Python equivalent to `PyCapsule_GetName`, so
    # resort to a hacky method for finding the signature.
    match = re.match(
        '<capsule object "(?P<signature>.+)" at 0x[A-Fa-f0-9]+>',
        str(capsule),
    )
    if match is None:
        logger.error(f"Unexpected capsule name format: {capsule}")
        raise ValueError('Unexpected capsule name {}'.format(capsule))

    signature = match.group('signature')
    match = re.match('(?P<return_type>.+) \\((?P<arg_types>.+)\\)', signature)
    if match is None:
        logger.error(f"Unexpected signature format: {signature}")
        raise ValueError('Unexpected signature {}'.format(signature))

    args = [
        arg_type for arg_type in match.group('arg_types').split(', ')
        if arg_type != 'int __pyx_skip_dispatch'
    ]
    parsed_signature = [match.group('return_type')] + args
    logger.debug(f"Parsed signature: {parsed_signature}")
    return parsed_signature


def de_mangle_function_name(mangled_name):
    logger.debug(f"De-mangling function name: {mangled_name}")
    match = re.match('(__pyx_fuse(_[0-9])*)?(?P<name>.+)', mangled_name)
    if match is None:
        logger.error(f"Unexpected mangled name: {mangled_name}")
        raise ValueError('Unexpected mangled name {}'.format(mangled_name))

    demangled = match.group('name')
    logger.debug(f"De-mangled name: {demangled}")
    return demangled


def get_signatures_from_pyx_capi():
    logger.info("Getting signatures from Pyx C API")
    signature_to_pointer = {}

    for mangled_name, capsule in cysc.__pyx_capi__.items():
        numba_signature = [
            CYTHON_TO_NUMBA.get(t) for t in parse_capsule_name(capsule)
        ]
        if any(t is None for t in numba_signature):
            logger.warning(f"Unknown type in signature for{mangled_name}, skipping")
            # We don't know how to handle this kernel yet.
            continue

        signature_to_pointer[(mangled_name, *numba_signature)] = capsule

    logger.info(f"Collected {len(signature_to_pointer)} signatures")
    return signature_to_pointer


def generate_signatures_dicts(signature_to_pointer):
    logger.info("Generating signatures dictionaries")
    name_to_numba_signatures = collections.defaultdict(list)
    name_and_types_to_pointer = {}
    for mangled_name, *signature in signature_to_pointer.keys():
        name = de_mangle_function_name(mangled_name)
        name_to_numba_signatures[name].append(
            tuple(signature[1:])
        )

        key = (name,) + tuple(signature[1:])

        address = (
            get_cython_function_address('scipy.special.cython_special', mangled_name)
        )
        ctypes_signature = [NUMBA_TO_CTYPES[t] for t in signature]
        ctypes_cast = (
            ctypes.CFUNCTYPE(*ctypes_signature)
        )
        name_and_types_to_pointer[key] = ctypes_cast(address)
        logger.debug(f"Registered function {key} at address {address}")

    name_to_numba_signatures = {
        name: tuple(signatures)
        for name, signatures in name_to_numba_signatures.items()
    }
    logger.info("Finished generating signatures dictionaries")
    return name_to_numba_signatures, name_and_types_to_pointer


signature_to_pointer = get_signatures_from_pyx_capi()
name_to_numba_signatures, name_and_types_to_pointer = generate_signatures_dicts(signature_to_pointer)
