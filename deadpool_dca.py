#########################################################################
# deadpool_dca is a Python library to help extracting execution traces  #
# from whiteboxes and convert them into traces compatible with          #
# Daredevil or Riscure Inspector                                        #
#                                                                       #
# It requires Tracer (TracerPIN or TracerGrind)                         #
# and outputs binary traces that can be exploited by DPA tools.         #
#                                                                       #
# Copyright (C) 2016                                                    #
# Original author:   Phil Teuwen <phil@teuwen.org>                      #
# Contributors:                                                         #
#                                                                       #
# This program is free software: you can redistribute it and/or modify  #
# it under the terms of the GNU General Public License as published by  #
# the Free Software Foundation, either version 3 of the License, or     #
# any later version.                                                    #
#                                                                       #
# This program is distributed in the hope that it will be useful,       #
# but WITHOUT ANY WARRANTY; without even the implied warranty of        #
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         #
# GNU General Public License for more details.                          #
#                                                                       #
# You should have received a copy of the GNU General Public License     #
# along with this program.  If not, see <http://www.gnu.org/licenses/>. #
#########################################################################

import os
import glob
import struct
import random
import subprocess
import math

def processinput(iblock, blocksize):
    """processinput() helper function
   iblock: int representation of one input block
   blocksize: int (8 for DES, 16 for AES)
   returns a list of strings to be used as args for the target
   default processinput(): returns one string containing the block in hex
"""
    return ['%0*x' % (2*blocksize, iblock)]

def processoutput(output, blocksize):
    """processoutput() helper function
   output: string, textual output of the target
   blocksize: int (8 for DES, 16 for AES)
   returns a int, supposed to be the data block outputted by the target
   default processouput(): expects the output to be directly the block in hex
"""
    return int(output, 16)

class ARCH:
    i386  = 0
    amd64 = 1

class FILTER:
    def __init__(self, keyword, modes, condition, extract, extract_fmt):
        self.keyword=keyword
        self.modes=modes
        self.condition=condition
        self.extract=extract
        self.extract_fmt=extract_fmt
        self.record_info=False

class DEFAULT_FILTERS:
    # Bytes written on stack:
    stack_w1      =FILTER('stack_w1', ['W'], lambda stack_range, addr, size, data: stack_range[0] <= addr <= stack_range[1] and size == 1, lambda addr, size, data: data, '<B')
    stack_w4      =FILTER('stack_w4', ['W'], lambda stack_range, addr, size, data: stack_range[0] <= addr <= stack_range[1] and size == 4, lambda addr, size, data: data, '<I')
    # Low byte(s) address of data read from data segment:
    mem_addr1_rw1 =FILTER('mem_addr1_rw1', ['R', 'W'], lambda stack_range, addr, size, data: (addr < stack_range[0] or addr > stack_range[1]) and size == 1, lambda addr, size, data: addr & 0xFF, '<B')
    mem_addr1_rw4 =FILTER('mem_addr1_rw4', ['R', 'W'], lambda stack_range, addr, size, data: (addr < stack_range[0] or addr > stack_range[1]) and size == 4, lambda addr, size, data: addr & 0xFF, '<B')
    mem_addr2_rw1 =FILTER('mem_addr2_rw1', ['R', 'W'], lambda stack_range, addr, size, data: (addr < stack_range[0] or addr > stack_range[1]) and size == 1, lambda addr, size, data: addr & 0xFFFF, '<H')
    # Bytes read from data segment:
    mem_data_rw1  =FILTER('mem_data_rw1', ['R', 'W'], lambda stack_range, addr, size, data: (addr < stack_range[0] or addr > stack_range[1]) and size == 1, lambda addr, size, data: data, '<B')
    mem_data_rw4  =FILTER('mem_data_rw4', ['R', 'W'], lambda stack_range, addr, size, data: (addr < stack_range[0] or addr > stack_range[1]) and size == 4, lambda addr, size, data: data, '<I')

class Tracer(object):
    def __init__(self, target,
                   processinput,
                   processoutput,
                   arch,
                   blocksize,
                   tmptracefile,
                   addr_range,
                   stack_range,
                   filters,
                   tolerate_error,
                   debug):
        self.target=target
        self.processinput=processinput
        self.processoutput=processoutput
        self.arch=arch
        self.blocksize=blocksize
        if tmptracefile == 'default':
            self.tmptracefile="trace.tmp%05i" % random.randint(0, 100000)
        else:
            self.tmptracefile=tmptracefile
        self.addr_range=addr_range
        self.stack_range=stack_range
        if self.stack_range != 'default':
            self.stack_range=(int(stack_range[:stack_range.index('-')], 16), int(stack_range[stack_range.index('-')+1:], 16))
        if filters == 'default':
            self.filters=[DEFAULT_FILTERS.stack_w1, DEFAULT_FILTERS.mem_addr1_rw1, DEFAULT_FILTERS.mem_data_rw1]
        else:
            self.filters=filters
        self.tolerate_error=tolerate_error
        self.debug=debug

    def run(self, n, verbose=True):
        for i in range(n):
            iblock=random.randint(0, (1<<(8*self.blocksize))-1)
            oblock=self.get_trace(i, iblock)
            if verbose:
                print '%05i %0*X -> %0*X' % (i, 2*self.blocksize, iblock, 2*self.blocksize, oblock)

    def sample2event(self, sample, filtr):
        # returns (event number, optional list of details (mem_mode, item, ins_addr, mem_addr, mem_size, mem_data, src_line_info))
        # assuming serialized samples
        ievent=int(math.ceil(float(sample)/struct.calcsize(filtr.extract_fmt)/8))
        # Let's see if we've more info...
        eventlist=[]
        for filename in glob.glob('trace_%s_*.info' % filtr.keyword):
            with open(filename) as info:
                for i, line in enumerate(info):
                    if i+1 == ievent:
                        mem_mode, item, ins_addr, mem_addr, mem_size, mem_data = line.split()
                        item, ins_addr, mem_addr, mem_size, mem_data=int(item), int(ins_addr, 16), int(mem_addr, 16), int(mem_size), int(mem_data, 16)
                        try:
                            output=subprocess.check_output(['addr2line', '-e', self.target, '0x%X'%ins_addr])
                            output=output.split('/')[-1].strip()
                        except:
                            output=''
                        eventlist.append((mem_mode, item, ins_addr, mem_addr, mem_size, mem_data, output))
                    elif i > ievent:
                        break
        return (ievent, eventlist)

    def _exec(self, cmd_list, debug=None):
        if debug is None:
            debug=self.debug
        if debug:
            print ' '.join(cmd_list)
        if self.tolerate_error:
            output=subprocess.check_output(' '.join(cmd_list) + '; exit 0', shell=True)
        else:
            output=subprocess.check_output(cmd_list)
        if debug:
            print output
        return output

    def _trace_init(self, n, iblock, oblock):
        self._trace_meta=(n, iblock, oblock)
        self._trace_data={}
        self._trace_info={}
        for f in self.filters:
            self._trace_data[f.keyword]=[]
            self._trace_info[f.keyword]=[]

    def _trace_dump(self):
        n, iblock, oblock = self._trace_meta
        for f in self.filters:
            with open('trace_%s_%04i_%0*X_%0*X.bin'
                  % (f.keyword, n, 2*self.blocksize, iblock, 2*self.blocksize, oblock), 'wb') as trace:
                trace.write(''.join([struct.pack(f.extract_fmt, x) for x in self._trace_data[f.keyword]]))
            if f.record_info:
                with open('trace_%s_%0*X_%0*X.info'
                      % (f.keyword, 2*self.blocksize, iblock, 2*self.blocksize, oblock), 'wb') as trace:
                    for mem_mode, item, ins_addr, mem_addr, mem_size, mem_data in self._trace_info[f.keyword]:
                        trace.write("[%s] %7i %16X %16X %2i %0*X\n" % (mem_mode, item, ins_addr, mem_addr, mem_size, 2*mem_size, mem_data))
                f.record_info=False
        del(self._trace_data)
        del(self._trace_info)

    def _bin2meta(self, f):
        # There is purposely no internal link with run() data, everything is read again from files
        # because it may have been obtained from several instances running in parallel
        bytes_per_iblock_sample = struct.calcsize(f.extract_fmt)
        n=len(glob.glob('trace_%s_*' % f.keyword))
        assert n > 0
        traces_meta={}
        min_size=None
        for filename in glob.glob('trace_%s_*' % f.keyword):
            i,iblock,oblock=filename[len('trace_%s_' % f.keyword):-len('.bin')].split('_')
            assert self.blocksize==len(iblock)/2
            assert self.blocksize==len(oblock)/2
            filesize = os.path.getsize(filename)
            if not min_size or min_size > filesize:
                min_size = filesize
            traces_meta[filename] = [int(iblock, 16), int(oblock, 16)]
        ntraces = len(traces_meta)
        nsamples = min_size/bytes_per_iblock_sample*8
        return (ntraces, nsamples, min_size, traces_meta)

    def bin2daredevil(self, delete_bin=True):
        for f in self.filters:
            ntraces, nsamples, min_size, traces_meta = self._bin2meta(f)
            with open('%s_%i_%i.trace' % (f.keyword, ntraces, nsamples), 'wb') as filetrace,\
                 open('%s_%i_%i.input' % (f.keyword, ntraces, nsamples), 'wb') as fileinput,\
                 open('%s_%i_%i.output' % (f.keyword, ntraces, nsamples), 'wb') as fileoutput:
                for filename, (iblock, oblock) in traces_meta.iteritems():
                    fileinput.write(('%0*X' % (2*self.blocksize, iblock)).decode('hex'))
                    fileoutput.write(('%0*X' % (2*self.blocksize, oblock)).decode('hex'))
                    with open(filename, 'rb') as trace:
                        filetrace.write(serializechars(trace.read(min_size)))
                    if delete_bin:
                        os.remove(filename)
            with open('%s_%i_%i.config' % (f.keyword, ntraces, nsamples), 'wb') as fileconfig:
                config="""
[Traces]
files=1
trace_type=i
transpose=true
index=0
nsamples=%i
trace=%s %i %i

[Guesses]
files=1
guess_type=u
transpose=true
guess=%s %i %i
#guess=%s %i %i

[General]
threads=8
order=1
return_type=double
algorithm=DES
position=LUT/DES_SBOX
round=0
bitnum=all
bytenum=all
correct_key=0x%s
memory=4G
top=20
        """ % (nsamples, \
               '%s_%i_%i.trace' % (f.keyword, ntraces, nsamples), ntraces, nsamples, \
               '%s_%i_%i.input' % (f.keyword, ntraces, nsamples), ntraces, self.blocksize, \
               '%s_%i_%i.output' % (f.keyword, ntraces, nsamples), ntraces, self.blocksize, \
               '30 32 34 32 34 36 32 36')
                fileconfig.write(config)

    def bin2trs(self, delete_bin=True):
        for f in self.filters:
            ntraces, nsamples, min_size, traces_meta = self._bin2meta(f)
            with open('%s_%i_%i.trs' % (f.keyword, ntraces, nsamples), 'wb') as trs:
                trs.write('\x41\x04' + struct.pack('<I', ntraces))
                trs.write('\x42\x04' + struct.pack('<I', nsamples))
                # Sample Coding
                #   bit 8-6: 000
                #   bit 5:   integer(0) or float(1)
                #   bit 4-1: sample length in bytes (1,2,4)
                trs.write('\x43\x01' + chr(struct.calcsize('<B')))
                # Length of crypto data
                trs.write('\x44\x02' + struct.pack('<H', self.blocksize+self.blocksize))
                # End of header
                trs.write('\x5F\x00')
                for filename, (iblock, oblock) in traces_meta.iteritems():
                    trs.write(('%0*X%0*X' % (2*self.blocksize, iblock, 2*self.blocksize, oblock)).decode('hex'))
                    with open(filename, 'rb') as trace:
                        trs.write(serializechars(trace.read(min_size)))
                    if delete_bin:
                        os.remove(filename)

class TracerPIN(Tracer):
    def __init__(self, target,
                   processinput=processinput,
                   processoutput=processoutput,
                   arch=ARCH.amd64,
                   blocksize=16,
                   tmptracefile='default',
                   addr_range='default',
                   stack_range='default',
                   filters='default',
                   tolerate_error=False,
                   debug=False,
                   record_info=True):
        super(TracerPIN, self).__init__(target, processinput, processoutput, arch, blocksize, tmptracefile, addr_range, stack_range, filters, tolerate_error, debug)
        # Execution address range
        # 0 = all
        # 1 = filter system libraries
        # 2 = filter all but main exec
        # 0x400000-0x410000 = trace only specified address range
        if self.addr_range == 'default':
            self.addr_range=2
        if stack_range == 'default':
            if self.arch==ARCH.i386:
                self.stack_range =(0xff000000, 0xffffffff)
            elif self.arch==ARCH.amd64:
                self.stack_range =(0x7fff00000000, 0x7fffffffffff)
        if record_info:
            for f in self.filters:
                f.record_info=True
    def get_trace(self, n, iblock):
        cmd_list=['Tracer', '-q', '1', '-b', '0', '-c', '0', '-i', '0', '-f', str(self.addr_range), '-o', self.tmptracefile, '--', self.target] + self.processinput(iblock, self.blocksize)
        output=self._exec(cmd_list)
        oblock=self.processoutput(output, self.blocksize)
        self._trace_init(n, iblock, oblock)
        with open(self.tmptracefile, 'r') as trace:
            for line in iter(trace.readline, ''):
                if len(line) > 2 and (line[1]=='R' or line[1]=='W'):
                    mem_mode=line[1]
                    item=int(line[4:13])
                    ins_addr=int(line[14:29], 16)
                    mem_addr=int(line[85:99], 16)
                    mem_size=int(line[105:107])
                    mem_data=int(line[114:].replace(" ",""), 16)
                    for f in self.filters:
                        if mem_mode in f.modes and f.condition(self.stack_range, mem_addr, mem_size, mem_data):
                            if f.record_info:
                                self._trace_info[f.keyword].append((mem_mode, item, ins_addr, mem_addr, mem_size, mem_data))
                            self._trace_data[f.keyword].append(f.extract(mem_addr, mem_size, mem_data))
        self._trace_dump()
        if not self.debug:
            os.remove(self.tmptracefile)
        return oblock

    def run_once(self, iblock=None, tracefile=None):
        if iblock is None:
            iblock=random.randint(0, (1<<(8*self.blocksize))-1)
        if tracefile is None:
            tracefile = self.tmptracefile
        cmd_list=['Tracer', '-f', str(self.addr_range), '-o', tracefile, '--', self.target] + self.processinput(iblock, self.blocksize)
        output=self._exec(cmd_list, debug=True)

class TracerGrind(Tracer):
    def __init__(self, target,
                   processinput=processinput,
                   processoutput=processoutput,
                   arch=ARCH.amd64,
                   blocksize=16,
                   tmptracefile='default',
                   addr_range='default',
                   stack_range='default',
                   filters='default',
                   tolerate_error=False,
                   debug=False,
                   record_info=False):
        super(TracerGrind, self).__init__(target, processinput, processoutput, arch, blocksize, tmptracefile, addr_range, stack_range, filters, tolerate_error, debug)
        # Execution address range
        # Valgrind: reduce at least to 0x400000-0x3ffffff to avoid self-tracing
        if addr_range == 'default':
            self.addr_range='0x400000-0x3ffffff'
        if stack_range == 'default':
            if self.arch==ARCH.i386:
                self.stack_range =(0xf0000000, 0xffffffff)
            if self.arch==ARCH.amd64:
                self.stack_range =(0xff0000000, 0xfffffffff)
        if record_info:
            raise ValueError("Sorry, option not yet supported!")

    def get_trace(self, n, iblock):
        cmd_list=['valgrind', '--quiet', '--trace-children=yes', '--tool=tracergrind', '--filter='+str(self.addr_range), '--vex-iropt-register-updates=allregs-at-mem-access', '--output='+self.tmptracefile+'.grind', self.target] + self.processinput(iblock, self.blocksize)
        output=self._exec(cmd_list)
        oblock=self.processoutput(output, self.blocksize)
        output=subprocess.check_output("texttrace %s >(grep '^.M' > %s)" % (self.tmptracefile+'.grind', self.tmptracefile), shell=True, executable='/bin/bash')
        if not self.debug:
            os.remove(self.tmptracefile+'.grind')
        self._trace_init(n, iblock, oblock)
        with open(self.tmptracefile, 'r') as trace:
            for line in iter(trace.readline, ''):
                mem_mode=line[line.index('MODE')+6]
                mem_addr=int(line[line.index('START_ADDRESS')+15:line.index('START_ADDRESS')+31], 16)
                mem_size=int(line[line.index('LENGTH')+7:line.index('LENGTH')+9])
                mem_data=int(line[line.index('DATA')+6:].replace(" ",""), 16)
                for f in self.filters:
                    if mem_mode in f.modes and f.condition(self.stack_range, mem_addr, mem_size, mem_data):
                        self._trace_data[f.keyword].append(f.extract(mem_addr, mem_size, mem_data))
        self._trace_dump()
        if not self.debug:
            os.remove(self.tmptracefile)
        return oblock

    def run_once(self, iblock=None, tracefile=None):
        if iblock is None:
            iblock=random.randint(0, (1<<(8*self.blocksize))-1)
        if tracefile is None:
            tracefile = self.tmptracefile
        cmd_list=['valgrind', '--trace-children=yes', '--tool=tracergrind', '--filter='+str(self.addr_range), '--vex-iropt-register-updates=allregs-at-mem-access', '--output='+tracefile+'.grind', self.target] + self.processinput(iblock, self.blocksize)
        output=self._exec(cmd_list, debug=True)
        output=subprocess.check_output("texttrace %s %s" % (tracefile+'.grind',tracefile))
        os.remove(tracefile+'.grind')

def serializechars(s, _out={}):
    """Replaces each byte of the string by 8 bytes representing the bits, starting with their LSB
"""
    # Memoization using mutable dict
    if not _out:
        for b in range(256):
            n=b
            o=''
            for i in range(8):
                o+=chr(n&1)
                n=n>>1
            _out[chr(b)]=o
    return ''.join(_out[x] for x in s)
