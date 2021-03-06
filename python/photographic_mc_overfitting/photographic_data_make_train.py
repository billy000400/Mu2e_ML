"""
This script prepares input photos and output truths without random masking.
"""
# feasibility test
# detect outliner by CNN

import sys
import csv
from pathlib import Path
from collections import Counter
import timeit
import pickle
from copy import deepcopy

import numpy as np
import pandas as pd

from sqlalchemy import *

util_dir = Path.cwd().parent.joinpath('util')
sys.path.insert(1, str(util_dir))
from Config import extractor_config as Config
from Abstract import binning_objects
from TrackDB_Classes import *
from mu2e_output import *


def make_data_from_distribution(track_dir, mean, std, windowNum, resolution):

    # Billy: I'm quite confident that all major tracks(e-) have more than 9 hits
    hitNumCut = 20

    ### Construct Path Objects
    dp_list = ["dig.mu2e.CeEndpoint.MDC2018b.001002_00000011.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000012.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000014.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000020.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000024.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000044.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000136.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000149.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000150.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000169.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000172.art",\
                "dig.mu2e.CeEndpoint.MDC2018b.001002_00000192.art"]
    dp_name_iter = iter(dp_list)
    dp_name = next(dp_name_iter)
    db_file = track_dir.joinpath(dp_name+".db")
    cwd = Path.cwd()
    data_dir = cwd.parent.parent.joinpath('data')
    data_dir.mkdir(parents=True, exist_ok=True)
    photographic_train_dir = data_dir.joinpath('photographic_train')
    photographic_train_dir.mkdir(parents=True, exist_ok=True)
    input_file = photographic_train_dir.joinpath('inputs.npy')
    output_file = photographic_train_dir.joinpath('outputs.npy')

    ### pixel truth labels
    is_blank = np.array([1,0,0], dtype=np.float32)
    is_bg = np.array([0,1,0], dtype=np.float32)
    is_major = np.array([0,0,1], dtype=np.float32)

    ### initialize sqlite session
    # Connect to the database
    pinfo('Connecting to the track database')
    engine = create_engine('sqlite:///'+str(db_file))
    # make session
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine) # session factory
    session = Session() # session object

    ### make bins
    pinfo('Making tracking windows')

    # get a distribution of integers
    floats = np.random.normal(loc=mean, scale=std, size=windowNum)
    float_type_ints = np.around(floats)
    track_numbers = float_type_ints.astype(int)

    # get particles and the particle iterator
    ptcls = session.query(Particle).all()
    ptcl_iter = iter(ptcls)

    # get major tracks for each
    inputs = []
    outputs = []
    for idx, track_number in enumerate(track_numbers):
        sys.stdout.write(t_info(f'Parsing windows {idx+1}/{windowNum}', special='\r'))
        if idx+1 == windowNum:
            sys.stdout.write('\n')
        sys.stdout.flush()

        # get corresponding number of tracks in this window
        track_box = []
        track_found_number = 0
        while track_found_number < track_number:
            try:
                ptcl = next(ptcl_iter)
            except:
                pinfo('\nRun out of particles')
                dp_name = next(dp_name_iter)
                db_file = track_dir.joinpath(dp_name+".db")
                pinfo('Connecting to the next track database')
                engine = create_engine('sqlite:///'+str(db_file))
                Session = sessionmaker(bind=engine) # session factory
                session = Session() # session object
                ptcls = session.query(Particle).all()
                ptcl_iter = iter(ptcls)
                ptcl = next(ptcl_iter)
                track_box = [ptcl]
                track_found_number = 1

            strawHit_qrl = session.query(StrawHit).filter(StrawHit.particle==ptcl.id)
            hitNum = strawHit_qrl.count()
            if hitNum < hitNumCut:
                continue
            else:
                track_box.append(ptcl)
                track_found_number += 1

        # draw bbox for each track
        mcs = [ session.query(StrawDigiMC).filter(StrawDigiMC.particle==ptcl.id).all() for ptcl in track_box ]

        # xs, ys, zs are 2-D lists.
        # Every list inside corresponds to a particle
        # They are going to be the reference for drawing bounding boxes
        xs = [ [mc.x for mc in mcs_for_ptcl] for mcs_for_ptcl in mcs ]
        ys = [ [mc.y for mc in mcs_for_ptcl] for mcs_for_ptcl in mcs ]
        zs = [ [mc.z for mc in mcs_for_ptcl] for mcs_for_ptcl in mcs ]

        # flatten data means destroying the 2-D list structure so that you cannot
        # tell which (x,y,z) belong to which particle.
        # They will be collections of data of all particles in this window
        mcs_pos = [ [(x,y,z) for x, y, z in zip(xs_i, ys_i, zs_i)] for xs_i, ys_i, zs_i in zip(xs, ys, zs) ]
        mcs_pos_flatten = [ (x,y,z) for mcs_pos_i in mcs_pos for x,y,z in mcs_pos_i ]
        xs_flatten = [ x for x, y, z in mcs_pos_flatten]
        ys_flatten = [ y for x, y, z in mcs_pos_flatten]

        bboxes = [ [min(xs_i), max(xs_i), min(ys_i), max(ys_i)] for xs_i, ys_i in zip(xs, ys) ]

        for i, bbox in enumerate(bboxes):
            # make x bins and y bins for binning objects
            x_bins = [-810, bbox[0]-1, bbox[1]+1, 810]
            y_bins = [-810, bbox[2]-1, bbox[3]+1, 810]

            if bbox[0] < -810:
                pdebug('bbox xmin violated')
            elif bbox[1] > 810:
                pdebug('bbox xmax violated')
            elif bbox[2] < -810:
                pdebug('bbox ymin violated')
            elif bbox[3] > 810:
                pdebug('bbox ymax violated')

            # get position tuples in the bounding box
            pos_selected_by_x = binning_objects(mcs_pos_flatten, xs_flatten, x_bins)[2]

            ### debug snippet
            # x_all = deepcopy(xs_flatten)
            # x_all.sort()

            ### debug snippet
            # pos_selected_by_x_x_sort = [x for [x,y,z] in pos_selected_by_x ]
            # pos_selected_by_x_x_sort.sort()
            # x_by_x_min = pos_selected_by_x_x_sort[0]

            pos_selected_by_y = binning_objects(mcs_pos_flatten, ys_flatten, y_bins)[2]

            ### debug snippet
            # pos_selected_by_y_x_sort = [x for [x,y,z] in pos_selected_by_y ]
            # pos_selected_by_y_x_sort.sort()
            # x_by_y_min = pos_selected_by_y_x_sort[0]



            selected_mcs_pos = list(set(pos_selected_by_x).intersection(pos_selected_by_y))
            selected_mcs_x = [ x for [x,y,z] in selected_mcs_pos ]
            sorted_selected_mcs_x = deepcopy(selected_mcs_x)
            sorted_selected_mcs_x.sort()
            selected_mcs_y = [ y for [x,y,z] in selected_mcs_pos ]
            sorted_selected_mcs_y = deepcopy(selected_mcs_y)
            sorted_selected_mcs_y.sort()

            # create the blank input photo by resolution and the xy ratio
            xmin = sorted_selected_mcs_x[0]

            # if xmin != bbox[0]:
            #     print()
            #     print(bbox[0], x_by_x_min, x_by_y_min, xmin)
            #     print(bbox[2],bbox[3])
            #     pdebug(pos_selected_by_x)
            #     sys.exit()

            xmax = sorted_selected_mcs_x[-1]
            ymin = sorted_selected_mcs_y[0]
            ymax = sorted_selected_mcs_y[-1]
            x_delta = xmax - xmin
            y_delta = ymax - ymin
            ratio = y_delta/x_delta
            if ratio >= 1:
                xpixel = int(np.ceil(resolution/ratio))
                ypixel = resolution
                input_photo = np.zeros(shape=(ypixel,xpixel), dtype=np.float32 )
                output_truth = np.zeros(shape=(ypixel,xpixel,3), dtype=np.float32)
                output_truth[:,:,0] = 1
            else:
                xpixel = resolution
                ypixel = int(np.ceil(resolution*ratio))
                input_photo = np.zeros(shape=(ypixel,xpixel), dtype=np.float32)
                output_truth = np.zeros(shape=(ypixel,xpixel,3), dtype=np.float32)
                output_truth[:,:,0] = 1

            # setup the x and y grids that are for sorting particles
            xstep = x_delta/xpixel
            ystep = y_delta/ypixel
            xbins = [xmin + i*xstep for i in range(xpixel+1)]
            xbins[-1] = xbins[-1]+1
            ybins = [ymin + i*ystep for i in range(ypixel+1)]
            ybins[-1] = ybins[-1]+1

            ### fill the density in the blank photo and truth
            # first index is row

            hitTot3 = 0
            for pos in selected_mcs_pos:
                if pos in mcs_pos[i]:
                    hitTot3+=1

            bins_by_row = binning_objects(selected_mcs_pos, selected_mcs_y, ybins)[1:]

            hitTot2 = 0

            ## bug is here
            for row, bin in enumerate(bins_by_row):
                x_bin_flatten = [ x for (x,y,z) in bin]
                squares_by_column = binning_objects(bin, x_bin_flatten, xbins)[1:]
                for col, square in enumerate(squares_by_column):
                    density = len(square)#number density
                    input_photo[ypixel-row-1][col] = density
                    if density != 0 :
                        has_major = False
                        for pos in square:
                            if pos in mcs_pos[i]:
                                has_major = True
                                hitTot2 += 1
                                break
                        if has_major == True:
                            output_truth[ypixel-row-1][col] = is_major
                        else:
                            output_truth[ypixel-row-1][col] = is_bg


            major_pixels = np.nonzero((output_truth==[0,0,1]).all(axis=2))
            densities = input_photo[major_pixels]
            hitTot = densities.sum()
            if hitTot < hitNumCut:
                pdebug(f'Less than {hitNumCut}, detected {hitTot}, detection 2 {hitTot2}, detection 3 {hitTot3}')
                hitTot4 = 0
                for bin in bins_by_row:
                    for pos in bin:
                        if pos in mcs_pos[i]:
                            hitTot4+=1
                print(hitTot4)
                sys.exit()


            inputs.append(input_photo)
            outputs.append(output_truth)

    np.save(input_file, inputs)
    np.save(output_file, outputs)

    return photographic_train_dir, input_file, output_file


def make_data(C, mode):
    """
    This function helps determine which mode should be used when preparing training data.
    If mode is "normal", track number would fit a Gaussian distribution whose parameters were specificed in the configuration object before called.
    If mode is " " [Unfinished]
    """
    pstage("Making training data")


    if mode == "normal":
        track_dir = C.track_dir
        mean = C.trackNum_mean
        std = C.trackNum_std
        windowNum = C.window
        train_dir, X_file, Y_file = make_data_from_distribution(track_dir, mean, std, windowNum, resolution)

    C.set_inputs(train_dir, X_file, Y_file)
    cwd = Path.cwd()
    pickle_path = cwd.joinpath('photographic.train.config.pickle')
    pickle.dump(C, open(pickle_path, 'wb'))
    return C

if __name__ == "__main__":
    pbanner()
    psystem('Photographic track extractor')
    pmode('Testing Feasibility')
    pinfo('Input DType for testing: StrawDigiMC')

    track_str = '../../tracks'
    track_dir = Path(track_str)
    C = Config(track_dir)

    mode = 'normal'
    window = 1000 # unit: number of windows
    mean = 5
    std = 2
    resolution = 240

    track_dir = Path(track_str)
    C = Config(track_dir)

    C.set_distribution(mean, std)
    C.set_window(window)
    C.set_resolution(resolution)

    start = timeit.default_timer()
    make_data(C, mode)
    total_time = timeit.default_timer()-start
    print('\n')
    pinfo(f'Elapsed time: {total_time}(sec)')
