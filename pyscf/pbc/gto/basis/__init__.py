#!/usr/bin/env python
# -*- coding: utf-8
# Author: Qiming Sun <osirpt.sun@gmail.com>
#         Timothy Berkelbach <tim.berkelbach@gmail.com> 

import os
import pyscf.gto.basis
from pyscf.pbc.gto.basis import parse_cp2k

ALIAS = {
    'gthaugdzvp'  : 'gth-aug-dzvp.dat',
    'gthaugqzv2p' : 'gth-aug-qzv2p.dat',
    'gthaugqzv3p' : 'gth-aug-qzv3p.dat',
    'gthaugtzv2p' : 'gth-aug-tzv2p.dat',
    'gthaugtzvp'  : 'gth-aug-tzvp.dat',
    'gthdzv'      : 'gth-dzv.dat',
    'gthdzvp'     : 'gth-dzvp.dat',
    'gthqzv2p'    : 'gth-qzv2p.dat',
    'gthqzv3p'    : 'gth-qzv3p.dat',
    'gthszv'      : 'gth-szv.dat',
    'gthtzv2p'    : 'gth-tzv2p.dat',
    'gthtzvp'     : 'gth-tzvp.dat',
    'gthccdzvp'   : 'gth-cc-dzvp.dat',
    'gthcctzvp'   : 'gth-cc-tzvp.dat',
    'gthccqzvp'   : 'gth-cc-qzvp.dat',
    'gthszvmolopt'      : 'gth-szv-molopt.dat',
    'gthdzvpmolopt'     : 'gth-dzvp-molopt.dat',
    'gthtzvpmolopt'     : 'gth-tzvp-molopt.dat',
    'gthtzv2pmolopt'    : 'gth-tzv2p-molopt.dat',
    'gthszvmoloptsr'    : 'gth-szv-molopt-sr.dat',
    'gthdzvpmoloptsr'   : 'gth-dzvp-molopt-sr.dat',
}

def parse(string):
    '''Parse the basis text which is in CP2K format, return an internal
    basis format which can be assigned to :attr:`Mole.basis`

    Args:
        string : Blank linke and the lines of "BASIS SET" and "END" will be ignored

    Examples:

    >>> cell = gto.Cell()
    >>> cell.basis = {'C': gto.basis.parse("""
    ... C DZVP-GTH
    ...   2
    ...   2  0  1  4  2  2
    ...         4.3362376436   0.1490797872   0.0000000000  -0.0878123619   0.0000000000
    ...         1.2881838513  -0.0292640031   0.0000000000  -0.2775560300   0.0000000000
    ...         0.4037767149  -0.6882040510   0.0000000000  -0.4712295093   0.0000000000
    ...         0.1187877657  -0.3964426906   1.0000000000  -0.4058039291   1.0000000000
    ...   3  2  2  1  1
    ...         0.5500000000   1.0000000000
    ... #
    ... """)}
    '''
    return parse_cp2k.parse(string)

def load(file_or_basis_name, symb):
    '''Convert the basis of the given symbol to internal format

    Args:
        file_or_basis_name : str
            Case insensitive basis set name. Special characters will be removed.
        symb : str
            Atomic symbol, Special characters will be removed.

    Examples:
        Load DZVP-GTH of carbon 

    >>> cell = gto.Cell()
    >>> cell.basis = {'C': load('gth-dzvp', 'C')}
    '''
    if os.path.isfile(file_or_basis_name):
        try:
            return parse_cp2k.load(file_or_basis_name, symb)
        except RuntimeError:
            with open(file_or_basis_name, 'r') as fin:
                return parse_cp2k.parse(fin.read())

    name = file_or_basis_name.lower().replace(' ', '').replace('-', '').replace('_', '')
    if name not in ALIAS:
        return pyscf.gto.basis.load(file_or_basis_name, symb)
    basmod = ALIAS[name]
    symb = ''.join(i for i in symb if i.isalpha())
    b = parse_cp2k.load(os.path.join(os.path.dirname(__file__), basmod), symb)
    return b

