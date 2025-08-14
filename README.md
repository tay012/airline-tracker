**Data 301**

**Project 2**

## Motivation: Pandas, Data Cleaning, Feature Engineering, PCA, Kmeans, HDBSCAN

## Data:

You are given a subset of data from a marketing campaign. It contains
861 rows, each row has 29 columns. See Proj2_Data_Description.pdf for a
description of each feature.

## Notebook:

Project2_Student.ipynb -- please complete all TODOs in this notebook

## Requirements

Analyze data, deduce clusters, attempt to determine what clusters mean.

-   Please do not use any 'for' or 'while' loops when operating on
    DataFrames and Series, map and apply only for all significant
    operations.

-   Impute all missing Incomes -- do not use average of entire column,
    see notebook for suggestions.

-   Reduce number of features (PCA, combining columns etc.. ) see
    notebook

-   Drop any columns that provide no information

-   Convert dates to a format that is suitable for an algorithm that
    uses euclidean distance as a metric.

-   Fix any obvious outliers

-   Run PCA on remaining columns

-   Cluster Data using Kmeans and HDBSCAN algorithms

-   Please see TODO's in provided notebook.

-   Try to interpret the clusters generated. Or what traits distinguish
    one cluster from another (you will have to do several plots for
    this, Seaborn's pairplot may help). This is the hardest part of
    clustering,

## Grading

I will clone your repo, then run your notebook. I will verify that you
have pre processed the data appropriately, then verify your cluster
analysis and the conclusions you have drawn.
