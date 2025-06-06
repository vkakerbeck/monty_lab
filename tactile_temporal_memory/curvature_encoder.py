# Copyright 2025 Thousand Brains Project
# Copyright 2023 Numenta Inc.
#
# Copyright may exist in Contributors' modifications
# and/or contributions to the work.
#
# Use of this source code is governed by the MIT
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

import argparse
import hashlib
import os
import pickle
import time

import numpy as np
import ray
import torch


def hash_curvature_for_order(curvature):
    return int(int(hashlib.md5(curvature.tobytes()).hexdigest(), 16)) / (2**128)


@ray.remote
def hash_slice_for_order(s):
    return np.array(list(map(hash_curvature_for_order, s)))


def hash_curvature_for_sdr(curvature):
    rng = np.random.default_rng(
        int(int(hashlib.md5(curvature.tobytes()).hexdigest(), 16))
    )

    return rng.integers(0, N, size=1)


@ray.remote
def hash_slice_for_sdr(s):
    return np.array(list(map(hash_curvature_for_sdr, s)))


if __name__ == "__main__":

    ray.init()

    # args parser
    parser = argparse.ArgumentParser(description="Create SDRS for curvatures.")

    parser.add_argument(
        "-sdr_p",
        type=str,
        default=f"~/tbp/tbp.monty/projects/tactile_temporal_memory/tm_dataset",
        help="Enter SDR_YCBMeshDataset relative path in the form of: ~/path/of/dataset",
    )
    parser.add_argument(
        "-r",
        type=int,
        nargs="?",
        default=3,
        help="Hash radius for Gaussian curvature *amount*.",
    )
    parser.add_argument(
        "-d1",
        type=int,
        nargs="?",
        default=0,
        help="Point cloud start index (inclusive) to save from the YCB dataset.",
    )
    parser.add_argument(
        "-d2",
        type=int,
        nargs="?",
        default=10,
        help="Point cloud end index (exclusive) to save from the YCB dataset.",
    )
    parser.add_argument("-n", type=int, nargs="?", default=1024, help="Size of SDR.")
    parser.add_argument(
        "-w", type=int, nargs="?", default=7, help="Number of 'on' bits in the SDR."
    )

    args = parser.parse_args()

    sdr_dataset_path = os.path.expanduser(args.sdr_p)
    hash_radius = args.r
    data_range = (args.d1, args.d2)
    N = args.n
    W = args.w

    lower_limit, upper_limit = 0, 100

    assert N > (11 * W)

    # load dataset
    coordinate_dir = os.path.join(sdr_dataset_path, "coordinate_data")
    curvature_dir = os.path.join(sdr_dataset_path, "curvature_data")

    # get processed coordinates and curvatures
    processed_coord_file = os.path.join(coordinate_dir, "processed_coordinate_data.pkl")
    processed_curve_file = os.path.join(curvature_dir, "processed_curvature_data.pkl")

    if not os.path.exists(processed_coord_file) or not os.path.exists(
        processed_curve_file
    ):
        raise Exception(
            "Missing files. Please run `python process_data.py -sdr_p "
            "{0} -ycb_p <YCB objects relative path>`".format(args.sdr_p)
        )

    with open(processed_coord_file, "rb") as f:
        coordinates = pickle.load(f)
    with open(processed_curve_file, "rb") as f:
        curvatures = pickle.load(f)

    # create curvature dataset directory
    os.makedirs(curvature_dir, exist_ok=True)

    # 1D "cube" of neighborhood points
    add = torch.arange(-hash_radius, hash_radius + 1)

    assert len(add) >= W

    # for every specified object, convert its curvature data into an SDR
    for d in np.arange(*data_range):
        start_time = time.time()

        int_curvatures = curvatures[d]
        all_curvatures = np.arange(lower_limit, upper_limit + 1)

        # find "neighborhood" of curvatures
        int_curvatures = (torch.from_numpy(int_curvatures).unsqueeze(1) + add).numpy()
        all_curvatures = (torch.from_numpy(all_curvatures).unsqueeze(1) + add).numpy()

        hash_ordering = ray.get(
            [hash_slice_for_order.remote(s) for s in int_curvatures]
        )
        hash_ordering = np.stack(hash_ordering)

        hash_ordering_all = ray.get(
            [hash_slice_for_order.remote(s) for s in all_curvatures]
        )
        hash_ordering_all = np.stack(hash_ordering_all)

        os.makedirs(
            os.path.join(
                curvature_dir, "hash_radius={0}/hash_orders".format(hash_radius)
            ),
            exist_ok=True,
        )

        np.save(
            os.path.join(
                curvature_dir,
                "hash_radius={0}/hash_orders/orders{1}.npy".format(hash_radius, d),
            ),
            hash_ordering,
        )

        print("Saved hash ordering for object {0}".format(d))

        x_inds = (
            np.arange(int_curvatures.shape[0])
            .repeat(W)
            .reshape(int_curvatures.shape[0], W)
        )

        selected_curvatures = int_curvatures[
            x_inds, hash_ordering.argsort(axis=1)[:, -W:]
        ]

        sdr_slots = ray.get([hash_slice_for_sdr.remote(s) for s in selected_curvatures])
        sdr_slots = np.stack(sdr_slots).squeeze()

        sdr = np.zeros((int_curvatures.shape[0], N), dtype=np.uint8)
        sdr[x_inds, sdr_slots] = 1

        np.save(
            os.path.join(
                curvature_dir, "hash_radius={0}/sdr{1}.npy".format(hash_radius, d)
            ),
            sdr,
        )

        print("Saved SDR for object {0}".format(d))
        print("Time taken: {:.3f}".format(time.time() - start_time))
        print()

    ray.shutdown()
