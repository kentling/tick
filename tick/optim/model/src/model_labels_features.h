//
// Created by Stéphane GAIFFAS on 06/12/2015.
//

#ifndef TICK_OPTIM_MODEL_SRC_MODEL_LABELS_FEATURES_H_
#define TICK_OPTIM_MODEL_SRC_MODEL_LABELS_FEATURES_H_

// License: BSD 3 clause

#include "model.h"

#include <iostream>

class ModelLabelsFeatures : public virtual Model {
 protected:
  ulong n_samples, n_features;

  //! Labels vector
  SArrayDoublePtr labels;

  //! Features matrix (either sparse or not)
  SBaseArrayDouble2dPtr features;

 public:
  ModelLabelsFeatures(SBaseArrayDouble2dPtr features,
                      SArrayDoublePtr labels);

  const char *get_class_name() const override {
    return "ModelLabelsFeatures";
  }

  ulong get_n_samples() const override {
    return n_samples;
  }

  ulong get_n_features() const override {
    return n_features;
  }

  // TODO: add consts
  BaseArrayDouble get_features(ulong i) const override {
    return view_row(*features, i);
  }

  virtual double get_label(ulong i) const {
    return (*labels)[i];
  }

  virtual ulong get_rand_max() const {
    return n_samples;
  }

  ulong get_epoch_size() const override {
    return n_samples;
  }

  template<class Archive>
  void load(Archive & ar) {
    ar(CEREAL_NVP(n_samples) );

    ArrayDouble temp_labels;
    ArrayDouble2d temp_features;
    ar(cereal::make_nvp("labels", temp_labels));
    ar(cereal::make_nvp("features", temp_features));

    labels = temp_labels.as_sarray_ptr();
    features = temp_features.as_sarray2d_ptr();
  }

  template<class Archive>
  void save(Archive & ar) const {
    ar(CEREAL_NVP(n_samples));
    ar(cereal::make_nvp("labels", *labels));
    ar(cereal::make_nvp("features", *features));
  }
};

#endif  // TICK_OPTIM_MODEL_SRC_MODEL_LABELS_FEATURES_H_
