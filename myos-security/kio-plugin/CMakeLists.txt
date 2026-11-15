# "Windows compatibility" tab in the file Properties dialog (KDE Plasma 6).
# Built inside the image by iso/config/hooks/normal/0400-properties-tab.hook.chroot
cmake_minimum_required(VERSION 3.16)
project(myos-compat-tab LANGUAGES CXX)

find_package(ECM 6.0 REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})
include(KDEInstallDirs)
include(KDECMakeSettings)

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Widgets)
find_package(KF6 REQUIRED COMPONENTS KIO CoreAddons)

kcoreaddons_add_plugin(myos_compat_tab
    SOURCES compatplugin.cpp
    INSTALL_NAMESPACE "kf6/propertiesdialog")
target_link_libraries(myos_compat_tab PRIVATE
    Qt6::Widgets KF6::KIOCore KF6::KIOWidgets KF6::CoreAddons)
