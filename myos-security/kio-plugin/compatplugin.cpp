// "Windows compatibility" tab in the Properties dialog of .exe and .msi files.
// Its checkbox switches Proton on or off for that program. The setting lives in
// ~/.config/myos/exe-settings.json, which myos-run-exe reads when the program
// is started (after the virus check). The "Compress" button opens myos-compress,
// which shrinks the program's files on the disk.

#include <KFileItem>
#include <KPluginFactory>
#include <KPropertiesDialog>
#include <KPropertiesDialogPlugin>

#include <QCheckBox>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QProcess>
#include <QPushButton>
#include <QSaveFile>
#include <QStandardPaths>
#include <QVBoxLayout>

namespace
{
QString configPath(const QString &name)
{
    return QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation)
        + QStringLiteral("/myos/") + name;
}

QJsonObject readJson(const QString &path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}
}

class CompatPlugin : public KPropertiesDialogPlugin
{
    Q_OBJECT
public:
    CompatPlugin(QObject *parent, const QVariantList &args);
    void applyChanges() override;

private:
    QString m_path;
    QCheckBox *m_proton = nullptr;
    bool m_initial = false;
};

CompatPlugin::CompatPlugin(QObject *parent, const QVariantList &args)
    : KPropertiesDialogPlugin(parent)
{
    Q_UNUSED(args)
    const KFileItemList items = properties->items();
    if (items.count() != 1 || !items.first().isLocalFile()) {
        return;
    }
    m_path = QFileInfo(items.first().localPath()).canonicalFilePath();
    if (m_path.isEmpty()) {
        return;
    }

    const QJsonObject files = readJson(configPath(QStringLiteral("exe-settings.json")))
                                  .value(QStringLiteral("files")).toObject();
    QString runner = files.value(m_path).toObject().value(QStringLiteral("runner")).toString();
    if (runner.isEmpty()) {
        runner = readJson(configPath(QStringLiteral("security.json")))
                     .value(QStringLiteral("default_runner")).toString(QStringLiteral("wine"));
    }
    m_initial = (runner == QLatin1String("proton"));

    auto *page = new QWidget;
    auto *layout = new QVBoxLayout(page);
    auto *title = new QLabel(QStringLiteral("<b>Windows program</b>"), page);
    m_proton = new QCheckBox(QStringLiteral("Run this program with Proton (recommended for games)"), page);
    m_proton->setChecked(m_initial);
    auto *help = new QLabel(QStringLiteral(
        "When this is off, the program runs with Wine, which is best for normal programs "
        "and installers. Proton (Valve's Steam Play technology) runs DirectX games through "
        "Vulkan.\n\nIt is checked for viruses the first time it starts (ClamAV, VirusTotal, "
        "ANY.RUN), and whenever you scan it."), page);
    help->setWordWrap(true);
    auto *more = new QPushButton(QStringLiteral("More settings (Proton version, C: drive, launch options)…"), page);
    auto *scan = new QPushButton(QStringLiteral("Scan for viruses now"), page);
    auto *buttons = new QHBoxLayout;
    buttons->addWidget(more);
    buttons->addWidget(scan);
    buttons->addStretch(1);
    auto *diskTitle = new QLabel(QStringLiteral("<b>Disk space</b>"), page);
    auto *diskHelp = new QLabel(QStringLiteral(
        "Compressing makes the program's files smaller on the disk. It keeps working as before, "
        "because the files are unpacked while they are read."), page);
    diskHelp->setWordWrap(true);
    auto *compress = new QPushButton(QStringLiteral("Compress to save disk space…"), page);
    auto *diskButtons = new QHBoxLayout;
    diskButtons->addWidget(compress);
    diskButtons->addStretch(1);
    layout->addWidget(title);
    layout->addWidget(m_proton);
    layout->addWidget(help);
    layout->addLayout(buttons);
    layout->addSpacing(12);
    layout->addWidget(diskTitle);
    layout->addWidget(diskHelp);
    layout->addLayout(diskButtons);
    layout->addStretch(1);

    connect(m_proton, &QCheckBox::toggled, this, [this](bool) {
        setDirty(true);
        Q_EMIT changed();
    });
    const QString path = m_path;
    connect(more, &QPushButton::clicked, page, [path]() {
        QProcess::startDetached(QStringLiteral("myos-exe-properties"), {path});
    });
    connect(scan, &QPushButton::clicked, page, [path]() {
        QProcess::startDetached(QStringLiteral("myos-run-exe"), {QStringLiteral("--scan-only"), path});
    });
    connect(compress, &QPushButton::clicked, page, [path]() {
        QProcess::startDetached(QStringLiteral("myos-compress"), {path});
    });

    properties->addPage(page, QStringLiteral("Windows compatibility"));
}

void CompatPlugin::applyChanges()
{
    if (!m_proton || m_path.isEmpty() || m_proton->isChecked() == m_initial) {
        return;
    }
    const QString file = configPath(QStringLiteral("exe-settings.json"));
    QJsonObject root = readJson(file);
    QJsonObject files = root.value(QStringLiteral("files")).toObject();
    QJsonObject entry = files.value(m_path).toObject();
    entry.insert(QStringLiteral("runner"),
                 m_proton->isChecked() ? QStringLiteral("proton") : QStringLiteral("wine"));
    files.insert(m_path, entry);
    root.insert(QStringLiteral("version"), 1);
    root.insert(QStringLiteral("files"), files);

    QDir().mkpath(QFileInfo(file).absolutePath());
    QSaveFile out(file);
    if (out.open(QIODevice::WriteOnly)) {
        out.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
        out.commit();
    }
    m_initial = m_proton->isChecked();
}

K_PLUGIN_CLASS_WITH_JSON(CompatPlugin, "myos-compat.json")

#include "compatplugin.moc"
