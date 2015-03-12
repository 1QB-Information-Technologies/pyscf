#!/usr/bin/env python
# $Id$
# -*- coding: utf-8

import os
import random
import time
import tempfile
import numpy
import h5py
import pyscf.lib
import pyscf.lib.logger as logger
from pyscf.ao2mo import _ao2mo

# default ioblk_size is 256 MB

IOBUF_WORDS_PREFER = 1e8
IOBUF_ROW_MIN = 160

def full(mol, mo_coeff, erifile, dataname='eri_mo', tmpdir=None,
         intor='cint2e', aosym='s4', comp=1,
         max_memory=4000, ioblk_size=256, verbose=logger.WARN):
    general(mol, (mo_coeff,)*4, erifile, dataname, tmpdir,
            intor, aosym, comp, max_memory, ioblk_size, verbose)
    return erifile

def general(mol, mo_coeffs, erifile, dataname='eri_mo', tmpdir=None,
            intor='cint2e', aosym='s4', comp=1,
            max_memory=4000, ioblk_size=256, verbose=logger.WARN):
    time_0pass = (time.clock(), time.time())
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mol.stdout, verbose)

    klsame = iden_coeffs(mo_coeffs[2], mo_coeffs[3])

    nmoi = mo_coeffs[0].shape[1]
    nmoj = mo_coeffs[1].shape[1]
    nmok = mo_coeffs[2].shape[1]
    nmol = mo_coeffs[3].shape[1]
    nao = mo_coeffs[0].shape[0]
    aosym = _stand_sym_code(aosym)
    if aosym in ('s1', 's2ij', 'a2ij'):
        nao_pair = nao * nao
    else:
        nao_pair = guess_nao_pair(mol, nao)

    nij_pair = nmoi*nmoj
    nkl_pair = nmok*nmol

    if klsame and aosym in ('s4', 's2kl', 'a2kl', 'a4ij', 'a4kl', 'a4'):
        log.debug('k-mo == l-mo')
        mokl = numpy.asarray(mo_coeffs[2], order='F')
        klshape = (0, nmok, 0, nmok)
    else:
        mokl = numpy.asarray(numpy.hstack((mo_coeffs[2],mo_coeffs[3])), order='F')
        klshape = (0, nmok, nmok, nmol)

    if h5py.is_hdf5(erifile):
        feri = h5py.File(erifile)
        if dataname in feri:
            del(feri[dataname])
    else:
        feri = h5py.File(erifile, 'w')
    if comp == 1:
        chunks = (nmoj,nmol)
        h5d_eri = feri.create_dataset(dataname, (nij_pair,nkl_pair),
                                      'c16', chunks=chunks)
    else:
        chunks = (1,nmoj,nmol)
        h5d_eri = feri.create_dataset(dataname, (comp,nij_pair,nkl_pair),
                                      'c16', chunks=chnks)

    if nij_pair == 0 or nkl_pair == 0:
        feri.close()
        return erifile
    log.debug('num. MO ints = %.8g, require disk %.8g', \
              float(nij_pair)*nkl_pair*comp, nij_pair*nkl_pair*comp*16/1e6)

# transform e1
    swapfile = tempfile.NamedTemporaryFile(dir=tmpdir)
    half_e1(mol, mo_coeffs, swapfile.name, intor, aosym, comp,
            max_memory, ioblk_size, log)

    time_1pass = log.timer('AO->MO eri transformation 1 pass', *time_0pass)

    e1buflen = guess_e1bufsize(max_memory, ioblk_size,
                               nij_pair, nao_pair, comp)[0]
    mem_words = max_memory * 1e6 / 16
    iobuf_size = min(float(nkl_pair)/(nkl_pair+nao_pair)*mem_words,
                     IOBUF_WORDS_PREFER) * 16
    e2buflen = guess_e2bufsize(ioblk_size, nij_pair, nao_pair)[0]

    log.debug('step2: kl-pair (ao %d, mo %d), mem %.8g MB, '
              'ioblock (r/w) %.8g/%.8g MB', \
              nao_pair, nkl_pair, e2buflen*nao_pair*16/1e6,
              e2buflen*nij_pair*16/1e6, e2buflen*nkl_pair*16/1e6)

    fswap = h5py.File(swapfile.name, 'r')
    klaoblks = len(fswap['0'])
    ijmoblks = int(numpy.ceil(float(nij_pair)/e2buflen)) * comp
    ao_loc = numpy.array(mol.ao_loc_2c(), dtype=numpy.int32)
    tao = numpy.array(mol.tmap(), dtype=numpy.int32)
    ti0 = time_1pass
    buf = numpy.empty((e2buflen, nao_pair), dtype=numpy.complex)
    istep = 0
    for row0, row1 in prange(0, nij_pair, e2buflen):
        nrow = row1 - row0

        for icomp in range(comp):
            istep += 1
            tioi = 0
            log.debug('step 2 [%d/%d], [%d,%d:%d], row = %d', \
                      istep, ijmoblks, icomp, row0, row1, nrow)

            col0 = 0
            for ic in range(klaoblks):
                dat = fswap['%d/%d'%(icomp,ic)]
                col1 = col0 + dat.shape[1]
                buf[:nrow,col0:col1] = dat[row0:row1]
                col0 = col1
            ti2 = log.timer('step 2 [%d/%d], load buf'%(istep,ijmoblks), *ti0)
            tioi += ti2[1]-ti0[1]
            pbuf = _ao2mo.r_e2_(buf[:nrow], mokl, klshape, tao, ao_loc, aosym)

            tw1 = time.time()
            if comp == 1:
                h5d_eri[row0:row1] = pbuf
            else:
                h5d_eri[icomp,row0:row1] = pbuf
            tioi += time.time()-tw1

            ti1 = (time.clock(), time.time())
            log.debug('step 2 [%d/%d] CPU time: %9.2f, Wall time: %9.2f, I/O time: %9.2f', \
                      istep, ijmoblks, ti1[0]-ti0[0], ti1[1]-ti0[1], tioi)
            ti0 = ti1
    feri.close()
    fswap.close()

    log.timer('AO->MO eri transformation 2 pass', *time_1pass)
    log.timer('AO->MO eri transformation', *time_0pass)
    return erifile


# swapfile will be overwritten if exists.
def half_e1(mol, mo_coeffs, swapfile,
            intor='cint2e', aosym='s4', comp=1,
            max_memory=4000, ioblk_size=256, verbose=logger.WARN, ao2mopt=None):
    time0 = (time.clock(), time.time())
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(mol.stdout, verbose)

    ijsame = iden_coeffs(mo_coeffs[0], mo_coeffs[1])

    nmoi = mo_coeffs[0].shape[1]
    nmoj = mo_coeffs[1].shape[1]
    nao = mo_coeffs[0].shape[0]
    aosym = _stand_sym_code(aosym)
    if aosym in ('s1', 's2kl', 'a2kl'):
        nao_pair = nao * nao
    else:
        nao_pair = guess_nao_pair(mol, nao)
    nij_pair = nmoi * nmoj

    if  ijsame and aosym in ('s4', 's2ij', 'a2ij', 'a4ij', 'a4kl', 'a4'):
        log.debug('i-mo == j-mo')
        moij = numpy.asarray(mo_coeffs[0], order='F')
        ijshape = (0, nmoi, 0, nmoi)
    else:
        moij = numpy.asarray(numpy.hstack((mo_coeffs[0],mo_coeffs[1])), order='F')
        ijshape = (0, nmoi, nmoi, nmoj)

    e1buflen, mem_words, iobuf_words, ioblk_words = \
            guess_e1bufsize(max_memory, ioblk_size, nij_pair, nao_pair, comp)
# The buffer to hold AO integrals in C code
    aobuflen = int((mem_words - iobuf_words) // (nao*nao*comp))
    shranges = guess_shell_ranges(mol, e1buflen, aobuflen,
                                  aosym not in ('s1', 's2kl', 'a2kl'))
    if ao2mopt is None:
#        if intor == 'cint2e':
#            ao2mopt = _ao2mo.AO2MOpt(mol, intor, 'CVHFnr_schwarz_cond',
#                                     'CVHFsetnr_direct_scf')
#        elif intor == 'cint2e_spsp1':
#        elif intor == 'cint2e_spsp1spsp2':
#        else:
#            ao2mopt = _ao2mo.AO2MOpt(mol, intor)
        ao2mopt = _ao2mo.AO2MOpt(mol, intor)

    log.debug('step1: tmpfile %.8g MB', nij_pair*nao_pair*16/1e6)
    log.debug('step1: (ij,kl) = (%d,%d), mem cache %.8g MB, iobuf %.8g MB',
              nij_pair, nao_pair, mem_words*16/1e6, iobuf_words*16/1e6)

    fswap = h5py.File(swapfile, 'w')
    for icomp in range(comp):
        g = fswap.create_group(str(icomp)) # for h5py old version

    tao = numpy.array(mol.tmap(), dtype=numpy.int32)

    # transform e1
    ti0 = log.timer('Initializing ao2mo.outcore.half_e1', *time0)
    nstep = len(shranges)
    for istep,sh_range in enumerate(shranges):
        log.debug('step 1 [%d/%d], AO [%d:%d], len(buf) = %d', \
                  istep+1, nstep, *(sh_range[:3]))
        buflen = sh_range[2]
        iobuf = numpy.empty((comp,buflen,nij_pair), dtype=numpy.complex)
        nmic = len(sh_range[3])
        p0 = 0
        for imic, aoshs in enumerate(sh_range[3]):
            log.debug1('      fill iobuf micro [%d/%d], AO [%d:%d], len(aobuf) = %d', \
                       imic+1, nmic, *aoshs)
            buf = _ao2mo.r_e1_(intor, moij, ijshape, aoshs,
                               mol._atm, mol._bas, mol._env,
                               tao, aosym, comp, ao2mopt)
            iobuf[:,p0:p0+aoshs[2]] = buf
            p0 += aoshs[2]
        ti2 = log.timer('gen AO/transform MO [%d/%d]'%(istep+1,nstep), *ti0)

        e2buflen, chunks = guess_e2bufsize(ioblk_size, nij_pair, buflen)
        for icomp in range(comp):
            dset = fswap.create_dataset('%d/%d'%(icomp,istep),
                                        (nij_pair,iobuf.shape[1]), 'c16',
                                        chunks=None)
            for col0, col1 in prange(0, nij_pair, e2buflen):
                dset[col0:col1] = pyscf.lib.transpose(iobuf[icomp,:,col0:col1])
        ti0 = log.timer('transposing to disk', *ti2)
    fswap.close()
    return swapfile

def full_iofree(mol, mo_coeff, intor='cint2e', aosym='s4', comp=1,
                verbose=logger.WARN):
    erifile = tempfile.NamedTemporaryFile()
    general(mol, (mo_coeff,)*4, erifile.name, dataname='eri_mo',
            intor=intor, aosym=aosym, comp=comp,
            verbose=verbose)
    feri = h5py.File(erifile.name, 'r')
    return numpy.array(feri['eri_mo'])

def general_iofree(mol, mo_coeffs, intor='cint2e', aosym='s4', comp=1,
                   verbose=logger.WARN):
    erifile = tempfile.NamedTemporaryFile()
    general(mol, mo_coeffs, erifile.name, dataname='eri_mo',
            intor=intor, aosym=aosym, comp=comp,
            verbose=verbose)
    feri = h5py.File(erifile.name, 'r')
    return numpy.array(feri['eri_mo'])


def iden_coeffs(mo1, mo2):
    return (id(mo1) == id(mo2)) \
            or (mo1.shape==mo2.shape and numpy.allclose(mo1,mo2))

def prange(start, end, step):
    for i in range(start, end, step):
        yield i, min(i+step, end)

def guess_e1bufsize(max_memory, ioblk_size, nij_pair, nao_pair, comp):
    mem_words = max_memory * 1e6 / 16
# part of the max_memory is used to hold the AO integrals.  The iobuf is the
# buffer to temporary hold the transformed integrals before streaming to disk.
# iobuf is then divided to small blocks (ioblk_words) and streamed to disk.
    if mem_words > 2e8:
        iobuf_words = int(IOBUF_WORDS_PREFER) # 1.2GB
    else:
        iobuf_words = int(mem_words // 2)
    ioblk_words = int(min(ioblk_size*1e6/16, iobuf_words))

    e1buflen = int(min(iobuf_words//(comp*nij_pair), nao_pair))
    return e1buflen, mem_words, iobuf_words, ioblk_words

def guess_e2bufsize(ioblk_size, nrows, ncols):
    e2buflen = int(min(ioblk_size*1e6/16/ncols, nrows))
    e2buflen = max(e2buflen//IOBUF_ROW_MIN, 1) * IOBUF_ROW_MIN
    chunks = (IOBUF_ROW_MIN, ncols)
    return e2buflen, chunks

# based on the size of buffer, dynamic range of AO-shells for each buffer
def guess_shell_ranges(mol, max_iobuf, max_aobuf, aosym):
    ao_loc = mol.ao_loc_2c()

    accum = []

    if aosym:
        for i in range(mol.nbas):
            di = ao_loc[i+1] - ao_loc[i]
            for j in range(i+1):
                dj = ao_loc[j+1] - ao_loc[j]
                accum.append(di*dj)
    else:
        for i in range(mol.nbas):
            di = ao_loc[i+1] - ao_loc[i]
            for j in range(mol.nbas):
                dj = ao_loc[j+1] - ao_loc[j]
                accum.append(di*dj)

    ijsh_range = []
    buflen = 0
    ij_start = 0
    for ij, dij in enumerate(accum):
        buflen += dij
        if buflen > max_iobuf:
# to fill each iobuf, AO integrals may need to be fill to aobuf several times
            if max_aobuf < buflen-dij:
                ijdiv = []
                n0 = ij_start
                aobuf = 0
                for n in range(ij_start, ij):
                    aobuf += accum[n]
                    if aobuf > max_aobuf:
                        ijdiv.append((n0, n, aobuf-accum[n]))
                        n0 = n
                        aobuf = accum[n]
                ijdiv.append((n0, ij, aobuf))
            else:
                ijdiv = [(ij_start, ij, buflen-dij)]

            ijsh_range.append((ij_start, ij, buflen-dij, ijdiv))

            ij_start = ij
            buflen = dij

    ij = len(accum)

    if max_aobuf < buflen:
        ijdiv = []
        n0 = ij_start
        aobuf = 0
        for n in range(ij_start, ij):
            aobuf += accum[n]
            if aobuf > max_aobuf:
                ijdiv.append((n0, n, aobuf-accum[n]))
                n0 = n
                aobuf = accum[n]
        ijdiv.append((n0, ij, aobuf))
    else:
        ijdiv = [(ij_start, ij, buflen)]
    ijsh_range.append((ij_start, ij, buflen, ijdiv))
    return ijsh_range

def _stand_sym_code(sym):
    if isinstance(sym, int):
        return 's%d' % sym
    elif 's' == sym[0] or 'a' == sym[0]:
        return sym
    else:
        return 's' + sym

def guess_nao_pair(mol, nao):
    ao_loc = mol.ao_loc_2c()
    nao_pair = 0
    for i in range(mol.nbas):
        di = ao_loc[i+1] - ao_loc[i]
        for j in range(i+1):
            dj = ao_loc[j+1] - ao_loc[j]
            nao_pair += di * dj
    return nao_pair


if __name__ == '__main__':
    import time
    from pyscf import scf
    from pyscf import gto
    mol = gto.Mole()
    mol.verbose = 5
    mol.output = 'out_outcore'
    mol.atom = [
        ["O" , (0. , 0.     , 0.)],
        [1   , (0. , -0.757 , 0.587)],
        [1   , (0. , 0.757  , 0.587)]]

    mol.basis = {'H': 'cc-pvdz',
                 'O': 'cc-pvdz',}
    mol.build()
    n2c = mol.nao_2c()
    numpy.random.seed(1)
    mo = numpy.random.random((n2c,n2c)) + numpy.random.random((n2c,n2c))*1j

    eri0 = numpy.empty((n2c,n2c,n2c,n2c), dtype=numpy.complex)
    pi = 0
    for i in range(mol.nbas):
        pj = 0
        for j in range(mol.nbas):
            pk = 0
            for k in range(mol.nbas):
                pl = 0
                for l in range(mol.nbas):
                    buf = gto.getints_by_shell('cint2e', (i,j,k,l),
                                               mol._atm, mol._bas, mol._env)
                    di, dj, dk, dl = buf.shape
                    eri0[pi:pi+di,pj:pj+dj,pk:pk+dk,pl:pl+dl] = buf
                    pl += dl
                pk += dk
            pj += dj
        pi += di

    nao, nmo = mo.shape
    eri0 = numpy.dot(mo.T.conj(), eri0.reshape(nao,-1))
    eri0 = numpy.dot(eri0.reshape(-1,nao), mo)
    eri0 = eri0.reshape(nmo,nao,nao,nmo).transpose(2,3,0,1).copy()
    eri0 = numpy.dot(mo.T.conj(), eri0.reshape(nao,-1))
    eri0 = numpy.dot(eri0.reshape(-1,nao), mo)
    eri0 = eri0.reshape((nmo,)*4)

    print(time.clock())
    full(mol, mo, 'h2oeri.h5', max_memory=10, ioblk_size=5)
    with h5py.File('h2oeri.h5', 'r') as feri:
        eri1 = numpy.array(feri['eri_mo']).reshape((nmo,)*4)

    print(time.clock())
    print(numpy.allclose(eri0, eri1))
